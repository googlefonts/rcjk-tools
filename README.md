# Repository for secondary RoboCJK tools

Such as converters, compilers, testing and debugging tools.

Contents:

- `rcjktools`: a Python library, implementing a RoboCJK reader, `VarC` table reader/writer, and various other conversion tools
- `ttxv`: `ttx` with support for the `VarC` table
- `rcjk2ufo`: convert an `.rcjk` project folder to a UFO
- `buildvarc`: add a `VarC` table to a variable font
- `VarCoPreviewer.py`: a simple Mac-only Variable Components previewer tool for `.rcjk`, `.ufo` and `.ttf`
- `RoboCJKPreviewer.py`: similar to `VarCoPreviewer.py`, but only for `.rcjk`, showing the three-level RoboCJK component hierarchy

ToDo:

- Describe VarCo-enhanced UFO
- Describe workflow to build a VarC-enabled VF
