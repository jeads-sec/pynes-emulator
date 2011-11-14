import struct

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