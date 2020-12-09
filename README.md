# Repository for secondary RoboCJK tools

Such as converters, compilers, testing and debugging tools.

Contents:

- `rcjktools`: a Python library, implementing a RoboCJK reader, `VarC` table reader/writer, and various other conversion tools
- `ttxv`: Same as the `ttx` command line tool, but with support for the `VarC` table
- `rcjk2ufo`: command line tool to convert an `.rcjk` project folder to a UFO
- `buildvarc`: command line tool to add a `VarC` table to a variable font
- `VarCoPreviewer.py`: a simple Mac-only Variable Components previewer tool for `.rcjk`, `.ufo` and `.ttf`
- `RoboCJKPreviewer.py`: similar to `VarCoPreviewer.py`, but only for `.rcjk`, showing the three-level RoboCJK component hierarchy

ToDo:

- Describe VarCo-enhanced UFO
- Describe workflow to build a VarC-enabled VF
