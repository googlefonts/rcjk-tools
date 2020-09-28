# Export a VarCo UFO from a .rcjk project

import sys
from rcjktools.project import RoboCJKProject


rcjkPath, ufoPath = sys.argv[1:3]

project = RoboCJKProject(rcjkPath)
project.saveVarCoUFO(ufoPath, "GSCJKTest", "VarCo")
