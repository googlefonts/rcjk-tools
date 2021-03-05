from collections import defaultdict
from datetime import datetime
import math
import pathlib
from fontTools.ttLib import TTFont
import drawbot_skia.drawbot as db
from .project import RoboCJKProject


def iterNonEmptyGlyphs(font):
    # TODO: this currently only works for CFF-based OTF
    # We're looking at the raw unparsed bytecode: if it's
    # really short, the glyph contains no outlines.
    gs = font.getGlyphSet()
    for glyphName in gs.keys():
        g = gs[glyphName]
        if len(g._glyph.bytecode) > 2:
            yield glyphName


def readComponentInfo(path):
    componentInfo = {}
    with open(path) as f:
        for line in f:
            uni, hasOutline, hasComponents = line.split()
            uni = int(uni, 16)
            hasOutline = hasOutline == "True"
            hasComponents = hasComponents == "True"
            componentInfo[uni] = (hasOutline, hasComponents)
    return componentInfo


statusColors = [
    (1.0, 0.0, 0.0, 1.0),
    (1.0, 0.5, 0.0, 1.0),
    (1.0, 1.0, 0.0, 1.0),
    (0.0, 0.5, 1.0, 1.0),
    (0.0, 1.0, 0.5, 1.0),
]

_statusColorsSet = set(statusColors)


def getGlyphInfo(project, glyphName):
    glyph = project.characterGlyphGlyphSet.getGlyph(glyphName)
    hasOutline = not glyph.outline.isEmpty()
    hasComponents = bool(glyph.components)
    statusColor = (1.0, 0.0, 0.0, 1.0)
    colorString = glyph.lib.get("public.markColor")
    if colorString:
        color = tuple(float(x) for x in colorString.split(","))
        if color in _statusColorsSet:
            statusColor = color
    return hasOutline, hasComponents, statusColor


def makeProof(
        fontPaths,
        pdfPath,
        *,
        rcjkProject=None,
        characters=None,
        pageWidth=842,
        pageHeight=595,
        margin=20,
        cellSize=40,
        labelSize=9,
        lineGap=4,
        statusColorSize=4,
        ):

    if rcjkProject is not None:
        if not isinstance(rcjkProject, RoboCJKProject):
            rcjkProject = RoboCJKProject(rcjkProject)
    else:
        statusColorSize = 0
    statusColor = None
    pdfPath = pathlib.Path(pdfPath).resolve()

    numFonts = len(fontPaths)
    with TTFont(fontPaths[0], lazy=True) as font:
        cmap = font.getBestCmap()
        if characters is None:
            characters = sorted(cmap.keys())
            nonEmptyGlyphs = set(iterNonEmptyGlyphs(font))
            # Filter out empty characters
            characters = [char for char in characters if cmap[char] in nonEmptyGlyphs]

    areaWidth = pageWidth - 2 * margin
    areaHeight = pageHeight - 2 * margin

    cellHeight = numFonts * cellSize + labelSize + statusColorSize

    numHorCells = areaWidth // cellSize
    numVerCells = (areaHeight + lineGap) // (cellHeight + lineGap)

    numCharsPerPage = numHorCells * numVerCells
    numPages = math.ceil(len(characters) / numCharsPerPage)

    charIter = iter(characters)

    utcnow = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    fontFileNamesLabel = ", ".join(f"{index}: {p.name}" for index, p in enumerate(fontPaths, 1))

    colorCount = defaultdict(int)
    deepComponentsCharacterCount = 0

    db.newDrawing()
    for pageIndex in range(numPages):
        db.newPage(pageWidth, pageHeight)
        db.translate(margin, margin)
        db.font("Helvetica", 7)
        db.text(f"Page {pageIndex + 1} — {utcnow} — {fontFileNamesLabel}", (0, -5))
        for y in range(numVerCells):
            y = areaHeight - cellHeight * (y + 1) - lineGap * y
            for x in range(numHorCells):
                char = next(charIter, None)
                if char is None:
                    continue
                glyphName = cmap.get(char)
                x = x * cellSize
                db.fill(0)
                db.font("Helvetica")
                db.fontSize(labelSize * 0.85)
                db.text(f"U+{char:04X}", (x, y + cellHeight - labelSize * 0.95))
                glyphColor = (0,)
                if rcjkProject is not None and glyphName is not None:
                    hasOutline, hasComponents, statusColor = getGlyphInfo(rcjkProject, glyphName)
                    colorCount[statusColor] += 1
                    db.fill(*statusColor)
                    db.rect(x + 1, y, cellSize - 2, statusColorSize)
                    if hasOutline and hasComponents:
                        glyphColor = (1, 0.35, 0.35)
                    elif hasOutline:
                        glyphColor = (0.35, 0.35, 1)
                    elif hasComponents:
                        deepComponentsCharacterCount += 1
                db.fill(*glyphColor)
                for fontIndex, fontPath in enumerate(fontPaths):
                    db.fontSize(cellSize * 0.9)
                    db.font(fontPath)
                    db.text(chr(char), (x, y + 0.12 * cellSize + (numFonts - 1 - fontIndex) * cellSize + statusColorSize))

    if colorCount:
        addStatusPage(pageWidth, pageHeight, colorCount, deepComponentsCharacterCount)

    db.saveImage(pdfPath)


def addStatusPage(pageWidth, pageHeight, colorCount, deepComponentsCharacterCount):
    characterCount = sum(colorCount.values())
    rectWidth = 800
    rectHeight = 30
    relativeLabelSize = 0.5
    marginleft = (pageWidth - rectWidth)*.5
    marginbottom = ((pageHeight - rectHeight) * 0.5) + rectHeight * len(colorCount)

    db.newPage(pageWidth, pageHeight)
    db.font("Helvetica")
    db.fontSize(rectHeight * relativeLabelSize)
    db.translate(marginleft, marginbottom)

    for color in statusColors:
        if color not in colorCount:
            continue
        width = (rectWidth / characterCount) * colorCount[color]
        percent = round((100 / characterCount) * colorCount[color], 3)
        # if color is None:
        #     continue
        db.fill(*color)
        db.rect(0, 0, width, rectHeight)
        db.fill(0)
        db.text(f"{percent} %", (width + 6, rectHeight * (1 - relativeLabelSize)), align="left")
        db.translate(0, -rectHeight * 2)

    db.text(
        f"{100 * deepComponentsCharacterCount / characterCount:.1f} % "
        f"of the characters were made purely with deep components "
        f"({deepComponentsCharacterCount} of {characterCount} characters total)",
        (0, 0),
    )


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("fontpaths", nargs="+", help="One or more paths to font files.")
    parser.add_argument(
        "pdfpath",
        help="The path for the pdf output. If the parent folder does not exist, it will be created.",
    )
    parser.add_argument("--rcjkpath", help="The .rcjk project folder")
    parser.add_argument(
        "--characters", type=argparse.FileType(),
        help="A path to a text file to be used as character input. "
        "If omitted, all non-empty characters from the font will be used.",
    )

    args = parser.parse_args()
    characters = None
    if args.characters is not None:
        characters = sorted({ord(char) for char in args.characters.read() if char != "\n"})

    makeProof(args.fontpaths, args.pdfpath, rcjkProject=args.rcjkpath, characters=characters)


if __name__ == "__main__":
    main()
