#!/usr/bin/python
import struct
import sys
import logging
import time
import pygame

from NES_PPU import palette

try:
   import psyco
   psyco.full()
except ImportError:
   print "Could not import psyco, skipping..."

#SRAM Area: 0x6000 -> 0x7FFF
#PRG Area:  0x8000 -> 0xFFFF
#PC reset vector: 0xFFFC

#PPU Status Reg: 0x2002 (see http://web.textfiles.com/games/nestech.txt)
#Joystick 1: $4016
#Joystick 2: $4017

# VBlank notes
# 240 scanlines/VBlank on NTSC * 60 VBlanks/second = 14400 scanlines/second
# NES runs at 1789772.5 cycles/second
# ~ 124 cycles per scanline
# 29760 cycles for 240 scanlines

def bin(x): 
   return ''.join(x & (1 << i) and '1' or '0' for i in range(7,-1,-1))

LEVELS = {'debug': logging.DEBUG,
          'info': logging.INFO,
          'warning': logging.WARNING,
          'error': logging.ERROR,
          'critical': logging.CRITICAL}

class PyNESException(Exception):
   def __init__(self, string):
      self.err_msg = string
   def __str__(self):
      return self.err_msg

class NESProc:
   def __init__(self, nes_file, log_level='warning'):
      
      # Format: (opcode, length, cycles)
      # Reference: http://e-tradition.net/bytes/6502/6502_instruction_set.html
      self.INST_SET = { \
         '\xA0': (self.do_ldy, 2, 2), '\xA2': (self.do_ldx, 2, 2), \
         '\x4C': (self.do_jmp, 3, 3), '\x84': (self.do_sty, 2, 3), \
         '\xA9': (self.do_lda, 2, 2), '\x91': (self.do_sta, 2, 6), \
         '\x88': (self.do_dey, 1, 2), '\xD0': (self.do_bne, 2, 2), #TODO: conditinal cycles\
         '\xC0': (self.do_cpy, 2, 2), '\x78': (self.do_sei, 1, 2), \
         '\xAD': (self.do_lda, 3, 4), '\x10': (self.do_bpl, 2, 2), #TODO: conditional cycles\
         '\x8D': (self.do_sta, 3, 4), '\x29': (self.do_and, 2, 2), \
         '\xF0': (self.do_beq, 2, 2), '\x1D': (self.do_ora, 3, 4), #TODO: conditional cycles\
         '\xD8': (self.do_cld, 1, 2), '\x85': (self.do_sta, 2, 3), \
         '\x60': (self.do_rts, 1, 6), '\xC6': (self.do_dec, 2, 5), \
         '\x9A': (self.do_txs, 1, 2), '\x95': (self.do_sta, 2, 4), \
         '\x9D': (self.do_sta, 3, 5), '\xE8': (self.do_inx, 1, 2), \
         '\x20': (self.do_jsr, 3, 6), '\x8E': (self.do_stx, 3, 4), \
         '\xBD': (self.do_lda, 3, 4), '\xE0': (self.do_cpx, 2, 2), #TODO: conditional cycles \
         '\xB1': (self.do_lda, 2, 5), '\xC8': (self.do_iny, 1, 2), #TODO: conditional cycles \
         '\xE6': (self.do_inc, 2, 5), '\xCA': (self.do_dex, 1, 2),
         '\x58': (self.do_cli, 1, 2), '\xA6': (self.do_ldx, 2, 3),
         '\x86': (self.do_stx, 2, 3), '\xC9': (self.do_cmp, 2, 2),
         '\x18': (self.do_clc, 1, 2), '\x65': (self.do_adc, 2, 3),
         '\x40': (self.do_rti, 1, 6), '\x8C': (self.do_sty, 3, 4), \
         '\xA8': (self.do_tay, 1, 2), '\x99': (self.do_sta, 3, 5), \
         '\x98': (self.do_tya, 1, 2), '\xEE': (self.do_inc, 3, 6), \
         '\xEA': (self.do_nop, 1, 2), '\xA5': (self.do_lda, 2, 3), \
         '\x48': (self.do_pha, 1, 3), '\x8A': (self.do_txa, 1, 2), \
         '\x0A': (self.do_asl, 1, 2), '\x68': (self.do_pla, 1, 4), \
         '\xAA': (self.do_tax, 1, 2), '\x6C': (self.do_jmp, 3, 5), \
         '\x00': (self.do_brk, 1, 7), '\xB0': (self.do_bcs, 2, 2), \
         '\x2C': (self.do_bit, 3, 4), '\x09': (self.do_ora, 2, 2), \
         }
      self.interfaces = { \
         0x2000: ("PPU Control Reg 1", self.do_ppu_ctrl1_access), \
         0x2001: ("PPU Control Reg 2", None), \
         0x2002: ("PPU Status Reg", None), \
         0x2003: ("Sprite Memory Address", None), \
         0x2004: ("Sprite Memory Data", None), \
         0x2005: ("Screen Scroll Offsets", None), \
         0x2006: ("PPU Memory Address", self.do_ppu_addr_access), \
         0x2007: ("PPU Memory Data", self.do_ppu_data_access), \
         0x4014: ("Sprite Memory DMA", self.do_ppu_sprite_dma_access), \
         0x4016: ("Joystick 1", None), }
         
      self.cycle_count = 0
      self.A = 0
      self.X = 0
      self.Y = 0
      self.PC = 0x8000
      self.S = 0xFF
      self.P = {'C': 0, 'Z': 0, 'I': 0, 'D': 0, 'B': 0, 'V': 0, 'N': 0}
      
      self.irq = 0
      self.reset = 0
      self.nmi = 0
      
      # PPU info
      self.PPU_low = None
      self.PPU_high = None
      self.PPU_addr = None
      self.PPU_vblank_enable = False
      self.PPU_pattern_table = 0x0000
      self.vram = bytearray(0x4000)     #16kb of PPU RAM
      self.spr_ram = bytearray(0x100)   # 256 bytes of SPR-RAM
      self.sprites = [None]*64
      
      self.log = logging.getLogger("6502-core")
      self.ch = logging.StreamHandler()
      self.ch.setLevel(logging.DEBUG)
      self.log.addHandler(self.ch)
      self.log.setLevel(LEVELS[log_level])
      self.loglevel = LEVELS[log_level]
      self.logEnabled = True
            
      self.vblank = False
      self.nes_file = nes_file
      self.memory = bytearray(0x10000)  #64kb of main RAM
      if len(self.nes_file.prgs) == 1:
         self.write_memory(0x8000, self.nes_file.prgs[0]) #TODO: make better
         self.write_memory(0xC000, self.nes_file.prgs[0])
      elif len(self.nes_file.prgs) > 1:
         self.write_memory(0x8000, self.nes_file.prgs[0])
         self.write_memory(0xC000, self.nes_file.prgs[1])
      else:
         print "invalid length of PRG-ROM"
      
      pygame.init()
      self.window = pygame.display.set_mode((256,240))
         
   def do_ldx(self, data):
      if data[0] == '\xA2':
         val = ord(data[1])
         if self.logEnabled: self.log.debug("LDX $%02x" % val)
         self.X = val
         self.set_flags(self.X)
      elif data[0] == '\xA6':
         addr = ord(data[1])
         val = ord(self.read_memory(addr, 1))
         if self.logEnabled: self.log.debug("LDX [$%02x]" % addr)
         self.X = val
         self.set_flags(self.X)
   
   def do_ldy(self, data):
      addr = ord(data[1])
      if self.logEnabled: self.log.debug("LDY $%02x" % addr)
      self.Y = addr
      self.set_flags(self.Y)
      
   def do_lda(self, data):
      if data[0] == '\xA9':
         val = ord(data[1])
         if self.logEnabled: self.log.debug("LDA %02x" % val)
      elif data[0] == '\xAD':
         addr = struct.unpack('H', data[1:3])[0]
         val = ord(self.read_memory(addr, 1))
         if self.logEnabled: self.log.debug("LDA [$%04x] => $%02x" % (addr, val))
      elif data[0] == '\xBD':
         abs_addr = struct.unpack('H', data[1:3])[0]
         addr = abs_addr + self.X
         val = ord(self.read_memory(addr, 1))
         if self.logEnabled: self.log.debug("LDA [$%04x, X], X = $%02x => $%02x" % (abs_addr, self.X, val))
      elif data[0] == '\xB1':
         addr = ord(data[1]) + self.Y
         val = ord(self.read_memory(addr, 1))
         if self.logEnabled: self.log.debug("LDA [$%02x + Y], Y = $%02x => $%02x" % (addr, self.Y, val))
      elif data[0] == '\xA5':
         addr = ord(data[1])
         val = ord(self.read_memory(addr, 1))
         if self.logEnabled: self.log.debug("LDA [$%04x] => $%02x" % (addr, val))
         
      self.A = val & 0xFF
      self.set_flags(self.A)
      
   def do_sty(self, data):
      if data[0] == '\x84':
         addr = ord(data[1])
         if self.logEnabled: self.log.debug("STY $%02x" % addr)
      elif data[0] == '\x8C':
         addr = struct.unpack('H', data[1:3])[0]
         if self.logEnabled: self.log.debug("STY $%04x" % addr)
      else:
         self.log.error("Unknown STY opcode")
         return
      self.write_memory(addr, chr(self.Y))      
      
   def do_sta(self, data):
      if data[0] == '\x91':
         #TODO: Don't think this is fully complete
         addr = ord(data[1])
         if self.logEnabled: self.log.debug("STA $%02x" % addr)
      elif data[0] == '\x8D':
         addr = struct.unpack('H', data[1:3])[0]
         if self.logEnabled: self.log.debug("STA $%04x" % addr)
      elif data[0] == '\x85':
         addr = ord(data[1])
         if self.logEnabled: self.log.debug("STA $%02x" % addr)
      elif data[0] == '\x95':
         addr = ord(data[1]) + self.X
         if self.logEnabled: self.log.debug("STA $%02x, X, X = $%02x" % (ord(data[1]), self.X))
      elif data[0] == '\x9D':
         abs_addr = struct.unpack('H', data[1:3])[0]
         addr = abs_addr + self.X
         if self.logEnabled: self.log.debug("STA $%04x, X, X = $%02x" % (abs_addr, self.X))
      elif data[0] == '\x99':
         abs_addr = struct.unpack('H', data[1:3])[0]
         addr = abs_addr + self.Y
         if self.logEnabled: self.log.debug("STA $%04x, Y, Y = $%02x" % (abs_addr, self.Y)) 
      else:
         self.log.error("Unknown STA opcode!")
         return
      self.write_memory(addr, chr(self.A))
      
   def do_jmp(self, data):
      if data[0] == '\x4C':
         loc = struct.unpack('H', data[1:3])[0]
      elif data[0] == '\x6C':
         addr = struct.unpack('H', data[1:3])[0]
         loc = struct.unpack('H', self.read_memory(addr, 2))[0]
      else:
         self.log.error('Unknown JMP opcode')
         return
      if self.logEnabled: self.log.debug("JMP $%04x" % loc)
      return loc
      
   def do_dey(self, data):
      if self.logEnabled: self.log.debug("DEY")
      if self.Y == 0:
         self.Y = 0xFF
      else:
         self.Y -= 1
      self.set_flags(self.Y)
      
   def calc_rel_jmp(self, data):
      return self.PC + struct.unpack('b',data[1])[0] + self.INST_SET[data[0]][1]
         
   def do_bne(self, data):
      offset = self.calc_rel_jmp(data)
      if self.logEnabled: self.log.debug("BNE $%04x + $%02x => $%04x" % (self.PC, struct.unpack('b',data[1])[0], offset))
      if self.P['Z'] == 0:
         return offset
         
   def do_cpy(self, data):
      addr = ord(data[1])
      temp = self.Y - self.memory[addr]
      if temp == 0:
         self.P['Z'] = 1
      else:
         self.P['Z'] = 0
         
      if temp > 0x7F:
         self.P['N'] = 1
      else:
         self.P['N'] = 0
      
      if self.Y >= addr:
         self.P['C'] = 1
      else:
         self.P['C'] = 0
         
      if self.logEnabled: self.log.debug("CPY $%02x" % addr)
      
   def do_sei(self, data):
      if self.logEnabled: self.log.debug("SEI")
      self.P['I'] = 1
      
   def do_bpl(self, data):
      offset = self.calc_rel_jmp(data)
      if self.logEnabled: self.log.debug("BPL $%04x + $%02x => $%04x" % (self.PC, struct.unpack('b',data[1])[0], offset))
      if self.P['N'] == 0:
         return offset 
   
   def do_and(self, data): 
      addr = ord(data[1])
      if self.logEnabled: self.log.debug("AND $%02x" % addr)
      self.A &= addr
      self.set_flags(self.A)
   
   def do_beq(self, data):
      offset = self.calc_rel_jmp(data)
      if self.logEnabled: self.log.debug("BEQ $%04x + $%02x => $%04x" % (self.PC, struct.unpack('b',data[1])[0], offset))
      if self.P['Z'] == 1:
         return offset
   
   def do_ora(self, data):
      if data[0] == '\x1d':
         addr = struct.unpack('H', data[1:3])[0]
         val = ord(self.read_memory(addr, 1))
         if self.logEnabled: self.log.debug("ORA $%02x, X" % val)
      elif data[0] == '\x09':
         val = ord(data[1])
         if self.logEnabled: self.log.debug("ORA $%02x, X" % val)
      else:
         raise PyNESException('Unknown ORA opcode')  
      self.A = (self.X | val) & 0xFF
      self.set_flags(self.A)
            
   def do_cld(self, data):
      if self.logEnabled: self.log.debug("CLD")
      self.P['D'] = 0
      
   def do_rts(self, data):
      new_loc = self.pop_stack()
      if self.logEnabled: self.log.debug("RTS => $%04x" % new_loc)
      return new_loc
      
   def set_flags(self, val):
      if val == 0:
         self.P['Z'] = 1
      else:
         self.P['Z'] = 0
      if val & 0x80:
         self.P['N'] = 1
      else:
         self.P['N'] = 0
         
   def do_dec(self, data):
      if data[0] == "\xC6":
         addr = ord(data[1])
         if self.logEnabled: self.log.debug("DEC $%02x" % addr)
         temp = ord(self.read_memory(addr, 1))
         if temp == 0:
            temp = 0xFF
         else:
            temp -= 1
         self.write_memory(addr, chr(temp))
         self.set_flags(temp)
         
   def do_txs(self, data):
      self.S = self.X
      if self.logEnabled: self.log.debug("TXS")
      self.set_flags(self.S)
      
   def do_inx(self, data):
      self.X += 1
      if self.logEnabled: self.log.debug("INX")
      if self.X == 0x100:
         self.X = 0
      self.set_flags(self.X)

   def do_iny(self, data):
      self.Y += 1
      if self.logEnabled: self.log.debug("INY")
      if self.Y == 0x100:
         self.Y = 0
      self.set_flags(self.Y)
   
   def do_jsr(self, data):
      self.push_stack(self.PC + 3)
      new_loc = struct.unpack('H', data[1:3])[0]
      if self.logEnabled: self.log.debug("JSR $%04x" % new_loc)
      return new_loc
   
   def do_stx(self, data):
      if data[0] == '\x8E':
         addr = struct.unpack(   'H', data[1:3])[0]
         if self.logEnabled: self.log.debug("STX $%04x" % addr)
         self.write_memory(addr, chr(self.X))
      if data[0] == '\x86':
         addr = ord(data[1])
         if self.logEnabled: self.log.debug("STX $%02x" % addr)
         self.write_memory(addr, chr(self.X))
   
   def do_cpx(self, data):
      if data[0] == '\xE0':
         operand = ord(data[1])
         result = self.X - operand
         if result < 0:
            result = 256 + result #emulate 8-bit register
         self.set_flags(result)
         if self.X >= operand:
            self.C = 1
         else:
            self.C = 0
   
   def do_inc(self, data):
      if data[0] == '\xE6':
         addr = ord(data[1])
         if self.logEnabled: self.log.debug("INC $%02x" % addr)
      elif data[0] == '\xEE':
         addr = struct.unpack('H', data[1:3])[0]
         if self.logEnabled: self.log.debug("INC $%04x" % addr)
      else:
         self.log.error("Unknown INC opcode")
         
      val = ord(self.read_memory(addr, 1))
      val += 1
      if val == 0x100:
         val = 0
      self.write_memory(addr, chr(val))
      self.set_flags(val)
         
   def do_dex(self, data):
      self.X -= 1
      self.set_flags(self.X)
      if self.logEnabled: self.log.debug("DEX")
   
   def do_cli(self, data):
      self.P['I'] = 0
      if self.logEnabled: self.log.debug("CLI")
      
   def do_cmp(self, data):
      val = ord(data[1])
      result = (self.A - val) & 0xFF
      if self.A >= val:
         self.P['C'] = 1
      else:
         self.P['C'] = 0
      self.set_flags(self.A)
      if self.logEnabled: self.log.debug("CMP $%02x" % val)
   
   def do_clc(self, data):
      self.P['C'] = 0
      if self.logEnabled: self.log.debug("CLC")
   
   def do_adc(self, data):
      if data[0] == '\x65':
         addr = ord(data[1])
         val = ord(self.read_memory(addr, 1)[0])
         if self.logEnabled: self.log.debug("ADC $%02x" % addr)
         
      result = (self.A + val + self.P['C']) & 0xFF
      #if (result & 0x80) != (self.A & 80):
      #ref: http://nesdev.parodius.com/bbs/viewtopic.php?t=6331&sid=c635c096178295cde45bd5e7ba0d2ca5
      if (self.A ^ result) & (val ^ result) & 0x80:
         self.P['V'] = 1
      else:
         self.P['V'] = 0
         
      if result > 0xFF:
         self.P['C'] = 1
         result = 0xFF - result
      else:
         self.P['C'] = 0
         
      self.A = result & 0xFF
      
   def do_rti(self, data):
      if self.logEnabled: self.log.debug("RTI")
      self.set_flags(self.pop_stack())
      return self.pop_stack()
         
   def do_tay(self, data):
      if self.logEnabled: self.log.debug("TAY")
      self.Y = self.A
      self.set_flags(self.Y)
   
   def do_tax(self, data):
      if self.logEnabled: self.log.debug("TAX")
      self.X = self.A
      self.set_flags(self.X)
   
   def do_tya(self, data):
      if self.logEnabled: self.log.debug("TYA")
      self.A = self.Y
      self.set_flags(self.A)
   
   def do_nop(self, data):
      if self.logEnabled: self.log.debug("NOP")
      
   def do_pha(self, data):
      self.push_stack(self.A)
      if self.logEnabled: self.log.debug("PHA   ")
   
   def do_txa(self, data):
      if self.logEnabled: self.log.debug("TXA")
      self.A = self.X
      self.set_flags(self.A)
   
   def do_asl(self, data):
      if data[0] == '\x0A':
         self.P['C'] = self.A & 0x80
         self.A = (self.A * 2) & 0xFF
         self.set_flags(self.A)
         if self.logEnabled: self.log.debug("ASL A")
      else:
         self.log.error("Unknown ASL opcode")
         return
   
   def do_pla(self, data):
      self.A = self.pop_stack() & 0xFF
      self.set_flags(self.A)
      if self.logEnabled: self.log.debug("PLA")
      
   def do_brk(self, data):
      self.push_stack(self.PC)
      self.push_stack(self.get_all_flags())
      self.P['B'] = 1
      return self.irq
   
   def do_bcs(self, data):
      if self.logEnabled: self.log.debug("BCS")
      if self.P['C']:
         addr = ord(data[1])
         if addr > 127: addr = 127 - addr
         return self.PC + addr
   
   def do_bit(self, data):
      if data[0] == '\x2C':
         addr = struct.unpack('H', data[1:3])[0]
         val = ord(self.read_memory(addr, 1))
      else:
         self.log.error("Unknown BIT opcode")
         return
      if self.logEnabled: self.log.debug("BIT 0x%02x & 0x%02x" % (self.A, val))
      result = self.A & val
      if result == 0: 
         self.P['Z'] = 1 
      else: 
         self.P['Z'] = 0
      if val & 0x40: 
         self.P['V'] = 1 
      else: 
         self.P['V'] = 0
      if val & 0x80: 
         self.P['N'] = 1 
      else:
         self.P['N'] = 0
      
   
   #internal func
   def do_ppu_sprite_dma_access(self, is_write, val):
      if is_write:
         addr = struct.unpack('B', val)[0] * 0x100
         if self.logEnabled: self.log.debug("Writing data @ 0x%04x into SPR-RAM" % addr)
         sprite_mem = self.read_memory(addr, 256)
         self.spr_ram = sprite_mem
      
   def do_ppu_ctrl1_access(self, is_write, val):
      if is_write:
         data = struct.unpack('B', val)[0]
         if self.logEnabled: self.log.debug("Writing 0x%02x to PPU Control Register 1" % data)
         self.PPU_vblank_enable = data & 0x80
         if data & 0x8: 
            self.PPU_pattern_table = 0x1000
         else:
            self.PPU_pattern_table = 0x0000
      
   def do_ppu_addr_access(self, is_write, val):
      addr = struct.unpack('B', val)[0]
      if is_write:
         if self.logEnabled: self.log.debug("Writing 0x%02x to PPU Addr register!" % addr)
      else:
         if self.logEnabled: self.log.debug("Reading 0x%02x from PPU Addr register!" % addr)
      if self.PPU_high == None:
         self.PPU_high = addr
      else:
         self.PPU_low = addr
         
      if self.PPU_low != None and self.PPU_high != None:
         self.PPU_addr = self.PPU_high << 8 | self.PPU_low
         self.PPU_low = self.PPU_high = None
         if self.logEnabled: self.log.debug("PPU set to write to 0x%04x" % self.PPU_addr)
   
   def do_ppu_data_access(self, is_write, val):
      data = struct.unpack('B', val)[0]
      ret = None
      
      if self.PPU_addr:
         if is_write:
            if self.logEnabled: self.log.debug("Writing 0x%02x to PPU Memory @ 0x%04x!" \
               % (data, self.PPU_addr))
            self.vram[self.PPU_addr] = data
         else:
            if self.logEnabled: self.log.debug("Reading 0x%02x from PPU Memory @ 0x%04x!" \
               % (data, self.PPU_addr))
            ret = self.vram[self.PPU_addr]
         self.PPU_addr += 1
      else:
         if self.logEnabled: self.log.warning("Trying to access PPU memory with invalid PPU address")
      if ret:
         return ret
      
   def do_write_ram(self, addr, val):
      #for idx,byte in enumerate(val):
      #   self.memory[addr+idx] = byte
      self.memory[addr:addr+len(val)] = val
      '''for i in range(len(val)):
         self.memory[addr+i] = ord(val[i])'''
         
   def get_all_flags(self):
      return self.P['C'] | self.P['Z'] << 1 | self.P['I'] << 2 | \
         self.P['D'] << 3 | self.P['B'] << 4 | self.P['V'] << 5 | \
         self.P['N'] << 6
   
   def set_all_flags(self, value):
      self.P['C'] = value & 1
      self.P['Z'] = value & 2
      self.P['I'] = value & 4
      self.P['D'] = value & 8
      self.P['B'] = value & 16
      self.P['V'] = value & 32
      self.P['N'] = value & 64
      
   def stack_dump(self):
      if self.logEnabled: self.log.debug("Stack Dump")
      cur_addr = 0xFF
      output = ''
      while cur_addr >= self.S:
         output += "$%04x: $%04x\n" % (0x0100+cur_addr, struct.unpack('H', self.read_memory(0x0100 + cur_addr, 2))[0])
         cur_addr -= 2
      if self.logEnabled: self.log.debug(output)
      
   def push_stack(self, value):
      # Reference: http://www.obelisk.demon.co.uk/6502/registers.html
      # Points to lower 8-bits of stack address (0x0100 -> 0x0x01FF)
      # Points to next free stack location
      self.S -= 2
      self.write_memory(0x0100 + self.S, struct.pack('H', value))      
      if self.logEnabled: self.log.debug("Pushing $%04x" % value)
      self.stack_dump()
   
   def pop_stack(self):
      self.stack_dump()
      stack_val = self.read_memory(0x0100 + self.S, 2)
      self.S += 2
      return struct.unpack('H', stack_val)[0]
         
   # val = string of data to write
   def write_memory(self, addr, val):
      if self.interfaces.has_key(addr):
         if self.logEnabled: self.log.info("Writing to %s" % self.interfaces[addr][0])
         if self.interfaces[addr][1]:
            self.interfaces[addr][1](True, val)
         
      # First four areas are mirrored
      if addr >= 0 and addr < 0x800:
         self.do_write_ram(addr+0x800, val)
         self.do_write_ram(addr+0x1000, val)
         self.do_write_ram(addr+0x1800, val)
      elif addr >= 0x800 and addr < 0x1000:
         offset = addr-0x800
         self.do_write_ram(offset, val)
         self.do_write_ram(offset+0x1000, val)
         self.do_write_ram(offset+0x1800, val)
      elif addr >= 0x1000 and addr < 0x1800:
         offset = addr-0x1000
         self.do_write_ram(offset, val)
         self.do_write_ram(offset+0x800, val)
         self.do_write_ram(offset+0x1800, val)
      elif addr >= 0x1800 and addr < 0x2000:
         offset = addr-0x1800
         self.do_write_ram(offset, val)
         self.do_write_ram(offset+0x800, val)
         self.do_write_ram(offset+0x1000, val)
      
      self.do_write_ram(addr, val)  
   
   # returns a data string copy of the memory
   def read_memory(self, addr, length):
      if self.interfaces.has_key(addr):
         if self.logEnabled: self.log.info("Reading from %s" % self.interfaces[addr][0])
         if self.interfaces[addr][1]:
            self.interfaces[addr][1](False, addr)
            
      return str(self.memory[addr:addr+length])
      
   def render_sprite(self, data, coord, attr):
      s = pygame.Surface([8,8])
      for y in range(8):
         low_byte = data[y]
         high_byte = data[8+y]
         for x in range(8):
            if not (low_byte & x) and not (high_byte & x):
               color_sel = 0
            elif low_byte & x and not (high_byte & x):
               color_sel = 1
            elif not (low_byte & x) and high_byte & x:
               color_sel = 2
            else:
               color_sel = 3
            color_sel |= (attr & 0x3) << 2
            #print "%d,%d = %d" % (x,y,color_sel | (attr & 0x3) << 2)
            print "0x%02x " % self.vram[0x3f00+color_sel],
            s.set_at((x,y), palette[self.vram[0x3f00+color_sel]])
         print ""
      
      return s
   
   def update_screen(self):
      self.window.fill((0,0,0))
      c = pygame.color.Color(255,255,255)
      #for byte in self.vram[0x3f00:0x3f10]:
      #   print byte
      for i in range(0, 256, 4):
         (y_pos, pat_num, attr, x_pos) = struct.unpack("BBBB", str(self.spr_ram[i:i+4]))
         if pat_num:
            #print "Sprite %d: Pattern #%d" % (i/4, pat_num)
            #print "Pattern table @ 0x%04x" % self.PPU_pattern_table
            sprite = self.render_sprite(self.memory[self.PPU_pattern_table+pat_num*0x10:self.PPU_pattern_table+pat_num*0x10+0x10], (x_pos, y_pos), attr)
            
            #if not self.sprites[i/4]:
            self.sprites[i/4] = sprite
            #elif self.sprites[i/4].top != y_pos or self.sprites[i/4].left != x_pos:
            #   self.sprites[i/4].topleft = (x_pos, y_pos)
         else:
            continue
            
         #self.window.fill(c, self.sprites[i/4])
         self.window.blit(self.sprites[i/4], (x_pos,y_pos))
         if self.logEnabled: self.log.debug("SPR%d: X: %d Y: %d" % (i/4, x_pos, y_pos))
      #print self.sprites
      pygame.display.update()
   
   def parse_instruction(self, data):
      #print "Parsing: %02x" % ord(data[0])
      new_loc = None
      if self.INST_SET.has_key(data[0]):
         new_loc = self.INST_SET[data[0]][0](data)
      else:
         if self.logEnabled: self.log.error("Unknown Opcode:")
         output = ''
         for i in range(10):
            output += "%02x " % ord(self.read_memory(self.PC+i, 1))
            #print "%02x " % ord(char),
         if self.logEnabled: self.log.error(output)
         
         sys.exit()
         #print "Unknown Opcode - skipping"
         #new_loc = self.PC + 1
      
      # Increment PC
      if new_loc:
         self.PC = new_loc
      else:
         self.PC += self.INST_SET[data[:1]][1]
      #print "PC = $%04x" % self.PC
   
   def print_regs(self):
      if self.logEnabled: self.log.debug("A: $%02x, X: $%02x, Y: $%02x, S: $%04x, PC: $%04x, Cycles: %d" % \
         (self.A, self.X, self.Y, 0x0100 + self.S, self.PC, self.cycle_count))
      if self.logEnabled: self.log.debug("  [Flags] N: %d, V: %d, B: %d, D: %d, I: %d, Z: %d, C: %d" % \
         (self.P['N'], self.P['V'], self.P['B'], self.P['D'], \
         self.P['I'], self.P['Z'], self.P['C']))
   
   def run(self):   
      self.nmi = struct.unpack('H', self.read_memory(0xFFFA, 2))[0]
      self.irq = struct.unpack('H', self.read_memory(0xFFFE, 2))[0]
      self.reset = struct.unpack('H', self.read_memory(0xFFFC, 2))[0]
      if self.logEnabled: self.log.info("Reset $%04x" % self.reset)
      if self.logEnabled: self.log.info("NMI $%04x" % self.nmi)
      if self.logEnabled: self.log.info("IRQ $%04x" % self.irq)
      
      old_time = time.time()
      while True:
         #offset = self.PC - 0x8000
         
         #for char in self.nes_file.prgs[0][offset:offset+10]:
         if self.loglevel <= logging.DEBUG:
            output = ''
            for i in range(10):
               output += "%02x " % ord(self.read_memory(self.PC+i, 1))
               #print "%02x " % ord(char),
            if self.logEnabled: self.log.debug(output)
         
         if self.loglevel <= logging.DEBUG:
            self.print_regs()
         #data = self.nes_file.prgs[0][offset:offset+5]
         data = self.read_memory(self.PC, 5)
         self.parse_instruction(data)
         
         # VBlank emulation
         self.cycle_count += self.INST_SET[data[:1]][2]
         if self.cycle_count >= 29760 and self.vblank == False:
            print "Time delta: %f" % (time.time() - old_time)
            old_time = time.time()
            ppu_status = ord(self.read_memory(0x2002, 1))
            if self.logEnabled: self.log.info("VBlank ON: PPU Status: 0x%02x" % ppu_status)
            self.write_memory(0x2002, chr(ppu_status | 0x80))
            self.cycle_count = 0 
            self.vblank = True
            
            if self.P['I'] == 0 and self.PPU_vblank_enable:
               self.push_stack(self.PC)
               self.push_stack(self.get_all_flags())
               self.PC = self.nmi
         
         #TODO: how long does a VBlank last?
         #if self.cycle_count >= 59520 and self.vblank == True:
         #ref: http://wiki.nesdev.com/w/index.php/Clock_rate
         if self.cycle_count >= 2728 and self.vblank == True:
            self.update_screen()
            ppu_status = ord(self.read_memory(0x2002, 1))
            if self.logEnabled: self.log.info("VBlank OFF: PPU Status: 0x%02x" % ppu_status)
            self.write_memory(0x2002, chr(ppu_status & 0x7F))           
            self.vblank = False
                  
      
      
class NESFile:
   prgs = []
   chrs = []
   
   def __init__(self, filename=None):
      self.filename = filename
      try:
         self.rom = open(self.filename, 'rb')
      except:
         print "Unable to open ROM file: %s" % self.filename
         return False
   
   def parse(self):
      header = self.rom.read(16)
      print header
      (self.header, self.prg_count, self.chr_count, \
       self.mapping1, self.mapping2) = \
       struct.unpack("4sBBBB8x", header)
      if self.header[:4] != "NES\x1a":
         print "Checksum does not match!"
         return False  
      print "PRG: %d, CHR: %d, Mapping1: 0x%x, Mapping2: 0x%x" % \
          (self.prg_count, self.chr_count, self.mapping1, self.mapping2)
      for i in range(self.prg_count):
         self.prgs.append(self.rom.read(16384))
      for i in range(self.chr_count):
         self.chrs.append(self.rom.read(8192))
      self.title = self.rom.read(128)
      
      if self.prg_count == 1:
         #Mapper 0 (?), mirror memory into both banks
         self.prgs.append(self.prgs[0])
         
   def read_memory(self, addr, length):
      #TODO: handle more than 2 banks
      if addr < 0xC000 and addr >= 0x8000:
         offset = addr-0x8000
         return self.prgs[0][offset:offset+length]
      elif addr >= 0xC000 and addr <= 0xFFFF:
         offset = addr-0x8000
         return self.prgs[1][offset:offset+length]
   
   def make_sprite(self, data):
      smap = {('0','0'):0, ('0','1'):2, ('1','0'):1, ('1','1'):3}
      for i in range(8):
         a = bin(ord(data[i]))
         b = bin(ord(data[i+8]))
         for j in range(8):
            print smap[(a[j],b[j])],
         print "\n",
   
   def dump_chrs(self):
      for idx,data in enumerate(self.chrs):
         for i in range(0,len(data),16):
            print "0x%04x" % (i + 16 + self.prg_count * 0x4000 + idx * 0x2000)
            sprite = self.make_sprite(data[i:i+16])
            print "\n",
         
         '''for idx, char in enumerate(data):
            if idx % 8 == 0:
               print "\n",
            print bin(ord(char))'''

def main():
   if len(sys.argv) != 3:
      print "Usage: ./nes_parse.py [rom_file] [log_level]"
      return -1
   nes_file = NESFile(sys.argv[1])
   nes_file.parse()
   #nes_file.read_memory(0xC000,4)
   #nes_file.dump_chrs()
   proc = NESProc(nes_file, sys.argv[2])
   proc.run()

if __name__ == "__main__":      
   main()
   #import cProfile
   #cProfile.run('main()')