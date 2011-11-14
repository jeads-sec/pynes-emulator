import logging

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