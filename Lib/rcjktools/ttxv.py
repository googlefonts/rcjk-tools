import sys
from fontTools.ttLib import registerCustomTableClass
from fontTools.ttx import main as ttx_main


def main():
    registerCustomTableClass("VarC", "rcjktools.table_VarC", "table_VarC")
    sys.exit(ttx_main())
