import struct
import logging

from pynes import *

class NES_PPU:
    def __init__(self, nes_core, log_level='warning'):
        self.palette = [(0x75,0x75,0x75), (0x27, 0x1B, 0x8F), (0x37, 0x00, 0xBF), (0x84, 0x00, 0xA6), \
                        (0xBB,0x00,0x6A), (0xB7,0x00,0x1E), (0xB3,0x00,0x00), (0x91,0x26,0x00),
                        (0x7B,0x2B,0x00), (0x00,0x3E,0x00), (0x00,0x48,0x0D), (0x00,0x3C,0x22),
                        (0x00,0x2F,0x66), (0x00,0x00,0x00), (0x00,0x00,0x00), (0x05,0x05,0x05),
            
                        (0xC8,0xC8,0xC8), (0x00,0x59,0xFF), (0x44,0x3C,0xFF), (0xB7,0x33,0xCC),
                        (0xFF,0x33,0xAA), (0xFF,0x37,0x5E), (0xFF,0x37,0x1A), (0xD5,0x4B,0x00),
                        (0xC4,0x62,0x00), (0x3C,0x7B,0x00), (0x1E,0x84,0x15), (0x00,0x95,0x66),
                        (0x00,0x84,0xC4), (0x11,0x11,0x11), (0x09,0x09,0x09), (0x09,0x09,0x09),
            
                        (0xFF,0xFF,0xFF), (0x00,0x95,0xFF), (0x6F,0x84,0xFF), (0xD5,0x6F,0xFF),
                        (0xFF,0x77,0xCC), (0xFF,0x6F,0x99), (0xFF,0x7B,0x59), (0xFF,0x91,0x5F),
                        (0xFF,0xA2,0x33), (0xA6,0xBF,0x00), (0x51,0xD9,0x6A), (0x4D,0xD5,0xAE),
                        (0x00,0xD9,0xFF), (0x66,0x66,0x66), (0x0D,0x0D,0x0D), (0x0D,0x0D,0x0D),
            
                        (0xFF,0xFF,0xFF), (0x84,0xBF,0xFF), (0xBB,0xBB,0xFF), (0xD0,0xBB,0xFF),
                        (0xFF,0xBF,0xEA), (0xFF,0xBF,0xCC), (0xFF,0xC4,0xB7), (0xFF,0xCC,0xAE),
                        (0xFF,0xD9,0xA2), (0xCC,0xE1,0x99), (0xAE,0xEE,0xB7), (0xAA,0xF7,0xEE),
                        (0xB3,0xEE,0xFF), (0xDD,0xDD,0xDD), (0x11,0x11,0x11), (0x11,0x11,0x11)]
        
        self.nes_core = nes_core
        
        self.PPU_low = None
        self.PPU_high = None
        self.PPU_addr = None
        self.PPU_vblank_enable = False
        self.PPU_pattern_table = 0x0000
        self.vram = bytearray(0x4000)      #16kb of PPU RAM
        self.spr_ram = bytearray(0x100)    # 256 bytes of SPR-RAM
        self.sprites = [None]*64
        
        self.log = logging.getLogger("6502-ppu")
        self.ch = logging.StreamHandler()
        self.ch.setLevel(logging.DEBUG)
        self.log.addHandler(self.ch)
        self.log.setLevel(LEVELS[log_level])
        self.loglevel = LEVELS[log_level]
        self.logEnabled = True#self.nes_core.logEnabled
    
    def do_ppu_sprite_dma_access(self, is_write, val):
        if is_write:
            addr = struct.unpack('B', val)[0] * 0x100
            if self.logEnabled: self.log.debug("Writing data @ 0x%04x into SPR-RAM" % addr)
            sprite_mem = self.nes_core.read_memory(addr, 256)
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