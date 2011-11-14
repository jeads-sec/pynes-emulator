#!/usr/bin/python
from argparse import ArgumentParser

from pynes.nesfile import NESFile
from pynes.nesproc import NESProc

'''
try:
    import psyco
    psyco.full()
except ImportError:
    print "Could not import psyco, skipping..."
'''

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

if __name__=='__main__':
    parser = ArgumentParser(description="An NES emulator implemented in Python")
    parser.add_argument("rom_file", help="Input NES ROM file")
    parser.add_argument("-l", dest="log_level", default='warning',
            help="The logging level [debug, info, warning, error, critical]")
    
    args = parser.parse_args()
    
    nes_file = NESFile(args.rom_file)
    nes_file.parse()
    #nes_file.read_memory(0xC000,4)
    #nes_file.dump_chrs()
    proc = NESProc(nes_file, args.log_level)
    proc.run()
