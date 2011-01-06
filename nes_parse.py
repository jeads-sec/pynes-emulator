#!/usr/bin/python
import struct
import sys

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


class NESProc:
   def __init__(self, nes_file):
      
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
         '\x9A': (self.do_txs, 1, 2),}
      self.interfaces = {0x2002: "PPU Status Reg", 0x2006: "VRAM Address", \
         0x2007: "VRAM I/O", 0x4016: "Joystick 1"}
         
      self.cycle_count = 0
      self.A = 0
      self.X = 0
      self.Y = 0
      self.PC = 0x8000
      self.S = 0
      self.P = {'C': 0, 'Z': 0, 'I': 0, 'D': 0, 'B': 0, 'V': 0, 'N': 0}
      
      self.vblank = False
      self.nes_file = nes_file
      self.memory = bytearray(0x10000)  #64kb of main RAM
      self.vram = bytearray(0x4000)     #16kb of PPU RAM
      if len(self.nes_file.prgs) == 1:
         self.write_memory(0x8000, self.nes_file.prgs[0]) #TODO: make better
         self.write_memory(0xC000, self.nes_file.prgs[0])
      elif len(self.nes_file.prgs) > 1:
         self.write_memory(0x8000, self.nes_file.prgs[0])
         self.write_memory(0xC000, self.nes_file.prgs[1])
      else:
         print "invalid length of PRG-ROM"
         
   def do_ldx(self, data):
      addr = ord(data[1])
      print "LDX $%02x" % addr
      self.X = addr
      self.set_flags(self.X)
   
   def do_ldy(self, data):
      addr = ord(data[1])
      print "LDY $%02x" % addr
      self.Y = addr
      self.set_flags(self.Y)
      
   def do_lda(self, data):
      if data[0] == '\xA9':
         val = ord(data[1])
         print "LDA $%02x" % val
         self.A = val
         self.set_flags(self.A)
      elif data[0] == '\xAD':
         addr = struct.unpack('H', data[1:3])[0]
         val = self.read_memory(addr, 1)[0]
         print "LDA [$%04x] => $%02x" % (addr, val)
         self.A = val
         self.set_flags(self.A)
      
   def do_sty(self, data):
      addr = ord(data[1])
      print "STY $%02x" % addr
      self.write_memory(addr, chr(self.Y))
      
   def do_sta(self, data):
      if data[0] == '\x91':
         #TODO: Don't think this is fully complete
         addr = ord(data[1])
         print "STA $%02x" % addr
         self.write_memory(addr, chr(self.A))
      elif data[0] == '\x8D':
         addr = struct.unpack('H', data[1:3])[0]
         print "STA $%04x" % addr
         self.write_memory(addr, chr(self.A))
      elif data[0] == '\x85':
         addr = ord(data[1])
         print "STA $%02x" % addr
         self.write_memory(addr, chr(self.A))
      
   def do_jmp(self, data):
      loc = struct.unpack('H', data[1:3])[0]
      print "JMP $%04x" % loc
      return loc
      
   def do_dey(self, data):
      print "DEY"
      if self.Y == 0:
         self.Y = 0xFF
      else:
         self.Y -= 1
      self.set_flags(self.Y)
      
   def calc_rel_jmp(self, data):
      return self.PC + struct.unpack('b',data[1])[0] + self.INST_SET[data[0]][1]
         
   def do_bne(self, data):
      offset = self.calc_rel_jmp(data)
      print "BNE $%04x + $%02x => $%04x" % (self.PC, struct.unpack('b',data[1])[0], offset)
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
         
      print "CPY $%02x" % addr
      
   def do_sei(self, data):
      print "SEI"
      self.P['I'] = 1
      
   def do_bpl(self, data):
      offset = self.calc_rel_jmp(data)
      print "BPL $%04x + $%02x => $%04x" % (self.PC, struct.unpack('b',data[1])[0], offset)
      if self.P['N'] == 0:
         return offset 
   
   def do_and(self, data): 
      addr = ord(data[1])
      print "AND $%02x" % addr
      self.A &= addr
      self.set_flags(self.A)
   
   def do_beq(self, data):
      offset = self.calc_rel_jmp(data)
      print "BEQ $%04x + $%02x => $%04x" % (self.PC, struct.unpack('b',data[1])[0], offset)
      if self.P['Z'] == 1:
         return offset
   
   def do_ora(self, data):
      if data[0] == '\x1d':
         addr = struct.unpack('H', data[1:3])[0]
         val = self.read_memory(addr, 1)[0]
         print "ORA $%02x, X" % val
         self.A = self.X | val
         self.set_flags(self.A)
            
   def do_cld(self, data):
      print "CLD"
      self.P['D'] = 0
      
   def do_rts(self, data):
      print "RTS [FINISH ME!]" #TODO Finish
      
   def set_flags(self, val):
      if val == 0:
         self.P['Z'] = 1
      else:
         self.P['Z'] = 0
      if val > 0x7F:
         self.P['N'] = 1
      else:
         self.P['N'] = 0
         
   def do_dec(self, data):
      if data[0] == "\xC6":
         addr = ord(data[1])
         print "DEC $%02x" % addr
         temp = self.read_memory(addr, 1)[0]
         if temp == 0:
            temp = 0xFF
         else:
            temp -= 1
         self.write_memory(addr, chr(temp))
         self.set_flags(temp)
         
   def do_txs(self, data):
      self.S = self.X
      print "TXS"
      self.set_flags(self.S)
            
   def do_write_ram(self, addr, val):
      for idx,byte in enumerate(val):
         self.memory[addr+idx] = byte
         
   def write_memory(self, addr, val):
      try:
         print "Writing to %s" % self.interfaces[addr]
      except:
         pass
         
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
      else:
         self.do_write_ram(addr, val)  
   
   def read_memory(self, addr, length):
      try:
         print "Reading from %s" % self.interfaces[addr]
      except:
         pass
      return self.memory[addr:addr+length]
   
   def parse_instruction(self, data):
      #print "Parsing: %02x" % ord(data[0])
      try:
         new_loc = self.INST_SET[data[:1]][0](data)
      except:
         print "Unknown Opcode"
         #print "Unknown Opcode - skipping"
         #new_loc = self.PC + 1
         
      if new_loc:
         self.PC = new_loc
      else:
         self.PC += self.INST_SET[data[:1]][1]
      #print "PC = $%04x" % self.PC
   
   def print_regs(self):
      print "A: $%02x, X: $%02x, Y: $%02x, PC: $%02x, Cycles: %d" % \
         (self.A, self.X, self.Y, self.PC, self.cycle_count)
      print "  [Flags] N: %d, V: %d, B: %d, D: %d, I: %d, Z: %d, C: %d" % \
         (self.P['N'], self.P['V'], self.P['B'], self.P['D'], \
         self.P['I'], self.P['Z'], self.P['C'])
   
   def run(self):
      nmi = self.read_memory(0xFFFA, 2)[0]
      print "NMI $%04x" % nmi
      reset = self.read_memory(0xFFFC, 2)[0]
      print "Reset $%04x" % reset
      irq = self.read_memory(0xFFFE, 2)[0]
      print "IRQ $%04x" % irq
      while True:
         offset = self.PC - 0x8000
         
         for char in self.nes_file.prgs[0][offset:offset+10]:
            print "%02x " % ord(char),
         print "\n",
         self.print_regs()
         data = self.nes_file.prgs[0][offset:offset+5]
         self.parse_instruction(data)
         
         # VBlank emulation
         self.cycle_count += self.INST_SET[data[:1]][2]
         if self.cycle_count >= 29760 and self.vblank == False:
            ppu_status = self.read_memory(0x2002, 1)[0]
            print "VBlank ON: PPU Status: 0x%02x" % ppu_status
            self.write_memory(0x2002, chr(ppu_status | 0x80))
            self.vblank = True
         
         #TODO: how long does a VBlank last?
         if self.cycle_count >= 59520 and self.vblank == True:
            ppu_status = self.read_memory(0x2002, 1)[0]
            print "VBlank OFF: PPU Status: 0x%02x" % ppu_status
            self.write_memory(0x2002, chr(ppu_status & 0x7F))
            self.cycle_count = 0            
            self.vblank = False
            
         print "\n",
      
      
      
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
   if len(sys.argv) != 2:
      print "Usage: ./nes_parse.py [rom_file]"
      return -1
   nes_file = NESFile(sys.argv[1])
   nes_file.parse()
   #nes_file.read_memory(0xC000,4)
   #nes_file.dump_chrs()
   proc = NESProc(nes_file)
   proc.run()

if __name__ == "__main__":
   main()