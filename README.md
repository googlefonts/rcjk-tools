# Repository for secondary RoboCJK tools

...such as converters, compilers, testing and debugging tools.

Relates to the Robo-CJK tool: https://github.com/BlackFoundryCom/robo-cjk

Prototype implementation of the Variable Components proposal: https://github.com/BlackFoundryCom/variable-components-spec

## Contents

- `rcjktools`: a Python library, implementing a RoboCJK reader, `VarC` table reader/writer, and various other conversion tools
- `ttxv`: same as the `ttx` command line tool, but with support for the `VarC` table
- `rcjk2ufo`: command line tool to convert an `.rcjk` project folder to a `.ufo`
- `buildvarc`: command line tool to add a `VarC` table to a variable font
- `VarCoPreviewer.py`: a simple Mac-only Variable Components previewer tool for `.rcjk`, `.ufo` and `.ttf`
- `RoboCJKPreviewer.py`: similar to `VarCoPreviewer.py`, but only for `.rcjk`, showing the three-level RoboCJK component hierarchy

## RoboCJK to VarC-VF workflow

To build a VarC-enable Variable font from a RoboCJK project, these steps need to be performed:

Export the RoboCJK project as a VarCo-UFO (variable component data is in lib entries):
```
rcjk2ufo ProjectName.rcjk ProjectName.ufo -f TheFamilyName -s TheStyleName
```

Build the skeleton VF (the parts of the VarC-VF that are standard OT 1.8):
```
fontmake -m ProjectName.designspace -o variable
```

Add the `VarC` table:
```
buildvarc ProjectName.designspace variable_ttf/ProjectName-VF.ttf
```

## To document

- Describe VarCo-enhanced UFO
- Describe workflow to build a VarC-enabled VF
