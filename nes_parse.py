#!/usr/bin/python
import struct
import sys

#SRAM Area: 0x6000 -> 0x7FFF
#PRG Area:  0x8000 -> 0xFFFF
#PC reset vector: 0xFFFC

#PPU Status Reg: 0x2002 (see http://web.textfiles.com/games/nestech.txt)

def bin(x): 
   return ''.join(x & (1 << i) and '1' or '0' for i in range(7,-1,-1))


class NESProc:
   def __init__(self, nes_file):
      self.INST_SET = {'\xA0': (self.do_ldy, 2), '\xA2': (self.do_ldx, 2), \
         '\x4C': (self.do_jmp, 3), '\x84': (self.do_sty, 2), \
         '\xA9': (self.do_lda, 2), '\x91': (self.do_sta, 2), \
         '\x88': (self.do_dey, 1), '\xD0': (self.do_bne, 2), \
         '\xC0': (self.do_cpy, 2), '\x78': (self.do_sei, 2), \
         '\xAD': (self.do_lda, 3), '\x10': (self.do_bpl, 2)}
      self.A = 0
      self.X = 0
      self.Y = 0
      self.PC = 0x8000
      self.S = 0
      self.P = {'C': 0, 'Z': 0, 'I': 0, 'D': 0, 'B': 0, 'V': 0, 'N': 0}
      self.nes_file = nes_file
      self.memory = bytearray(0x10000)
      
   def do_load(self, val):
      if val < 0:
         self.P['N'] = 1
      else:
         self.P['N'] = 0
         
      if val == 0:
         self.P['Z'] = 1
      else:
         self.P['Z'] = 0
         
   def do_ldx(self, data):
      addr = ord(data[1])
      self.do_load(addr) 
      print "LDX $%02x" % addr
      self.X = addr
   
   def do_ldy(self, data):
      addr = ord(data[1])
      self.do_load(addr)
      print "LDY $%02x" % addr
      self.Y = addr
      
   def do_lda(self, data):
      if data[0] == '\xA9':
         val = ord(data[1])
         print "LDA $%02x" % addr
         self.do_load(val)
         self.A = addr
      elif data[0] == '\xAD':
         addr = struct.unpack('H', data[1:3])[0]
         val = self.read_memory(addr, 1)[0]
         print "LDA [$%04x] => $%02x" % (addr, val)
         self.do_load(val)
         self.A = val
      
   def do_sty(self, data):
      addr = ord(data[1])
      print "STY $%02x" % addr
      self.memory[addr] = chr(self.Y)
      
   def do_sta(self, data):
      addr = ord(data[1])
      print "STA $%02x" % addr
      self.memory[addr] = chr(self.A)
      
   def do_jmp(self, data):
      loc = struct.unpack('H', data[1:3])[0]
      print "JMP $%04x" % loc
      return loc
      
   def do_dey(self, data):
      print "DEY"
      if self.Y == 0:
         self.Y = 0xFF
         self.P['N'] = 1
      else:
         self.Y -= 1
         
   def do_bne(self, data):
      addr = ord(data[1])
      print "BNE $%04x + $%02x => $%04x" % (self.PC, addr, self.PC+addr)
      if self.P['Z'] == 0:
         return addr + self.PC
         
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
      addr = self.PC + ord(data[1])
      print "BPL $%04x" % addr
      if self.P['N'] == 0:
         return addr
         
      
   def read_memory(self, addr, length):
      return self.memory[addr:addr+length]
   
   def parse_instruction(self, data):
      #print "Parsing: %02x" % ord(data[0])
      new_loc = self.INST_SET[data[:1]][0](data)
      if new_loc:
         self.PC = new_loc
      else:
         self.PC += self.INST_SET[data[:1]][1]
      #print "PC = $%04x" % self.PC
   
   def print_regs(self):
      print "A: $%02x, X: $%02x, Y: $%02x, PC: $%02x" % \
         (self.A, self.X, self.Y, self.PC)
      print "  [Flags] N: %d, V: %d, B: %d, D: %d, I: %d, Z: %d, C: %d" % \
         (self.P['N'], self.P['V'], self.P['B'], self.P['D'], \
         self.P['I'], self.P['Z'], self.P['C'])
   
   def run(self):
      while True:
         offset = self.PC - 0x8000
         
         for char in self.nes_file.prgs[0][offset:offset+5]:
            print "%02x " % ord(char),
         print "\n",
         self.print_regs()
         self.parse_instruction(self.nes_file.prgs[0][offset:offset+5])
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