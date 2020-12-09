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

## To document

- Describe VarCo-enhanced UFO
- Describe workflow to build a VarC-enabled VF
