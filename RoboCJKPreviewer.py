import sys
import os
import objc
from AppKit import NSFormatter
from vanilla import *

import drawBot as db
from drawBot.ui.drawView import DrawView
from drawBot.drawBotDrawingTools import _drawBotDrawingTool
from drawBot.context.drawBotContext import DrawBotContext

try:
    from rcjktools.project import RoboCJKProject
except ImportError:
    libPath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Lib")
    assert libPath not in sys.path
    sys.path.append(libPath)
    from rcjktools.project import RoboCJKProject


def ClassNameIncrementer(clsName, bases, dct):
    orgName = clsName
    counter = 0
    while True:
        try:
            objc.lookUpClass(clsName)
        except objc.nosuchclass_error:
            break
        counter += 1
        clsName = orgName + str(counter)
    return type(clsName, bases, dct)


class UnicodesFormatter(NSFormatter, metaclass=ClassNameIncrementer):

    def stringForObjectValue_(self, unicodes):
        return " ".join(f"U+{u:04X}" for u in unicodes)

    # def getObjectValue_forString_errorDescription_(self, value, string, error):
    #     XXX



class RoboCJKPreviewer:

    def __init__(self, rcjkProjectPath):
        self.project = RoboCJKProject(rcjkProjectPath)
        self.glyphList = [dict(glyphName=glyphName, unicode=unicodes)
            for glyphName, unicodes in self.project.getGlyphNamesAndUnicodes().items()]
        self.glyphList.sort(key=lambda item: (item["unicode"], item["glyphName"]))

        self.w = Window((1000, 400), f"RoboCJK Previewer — {rcjkProjectPath}",
            minSize=(1000, 400), autosaveName="RoboCJKPreviewer")
        self.w.findGlyphField = EditText((10, 10, 180, 20), callback=self.findGlyphFieldCallback)
        self.w.axisSlider = Slider((210, 8, 180, 20), value=0, minValue=0, maxValue=1,
            callback=self.axisSliderCallback)

        top = 40
        columnDescriptions = [
            dict(title="glyph name", key="glyphName"),
            dict(title="unicode", key="unicode", formatter=UnicodesFormatter.alloc().init()),
        ]
        self.w.characterGlyphList = List((0, top, 200, 0), self.glyphList,
            columnDescriptions=columnDescriptions,
            allowsMultipleSelection=False,
            allowsSorting=False,
            showColumnTitles=False,
            drawFocusRing=False,
            selectionCallback=self.characterGlyphListSelectionChangedCallback)

        self.w.deepComponentList = List((200, top, 200, 0), [],
            allowsMultipleSelection=False,
            drawFocusRing=False,
            selectionCallback=self.deepComponentListSelectionChangedCallback)

        self.w.atomicElementList = List((400, top, 200, 0), [],
            allowsMultipleSelection=False,
            drawFocusRing=False,
            selectionCallback=self.atomicElementListSelectionChangedCallback)

        self.w.dbView = DrawView((600, 0, 0, 0))  # The DrawBot PDF view
        self.w.characterGlyphList.setSelection([])
        self.w.open()

    def findGlyphFieldCallback(self, sender):
        pat = sender.get().lower()
        if not pat:
            self.w.characterGlyphList.set(self.glyphList)
        else:
            items = []
            for item in self.glyphList:
                if pat in item["glyphName"].lower():
                    items.append(item)
            self.w.characterGlyphList.set(items)
        self.w.characterGlyphList.setSelection([])

    def characterGlyphListSelectionChangedCallback(self, sender):
        self.updateCurrentGlyph()
        deepComponents = [dcName for dcName, atomicElements in self._currentGlyphComponents]
        self.w.deepComponentList.set(deepComponents)
        self.w.deepComponentList.setSelection([])
        self.w.atomicElementList.set([])
        self.w.atomicElementList.setSelection([])

    def updateCurrentGlyph(self):
        sel = self.w.characterGlyphList.getSelection()
        if sel:
            glyphName = self.w.characterGlyphList[sel[0]]["glyphName"]
            outline, dcItems = self.project.drawCharacterGlyph(
                glyphName, location={"wght": self.w.axisSlider.get()})
        else:
            outline = None
            dcItems = []
        self._currentGlyphOutline = outline
        self._currentGlyphComponents = dcItems

    def deepComponentListSelectionChangedCallback(self, sender):
        sel = sender.getSelection()
        if sel:
            dcName, atomicElements = self._currentGlyphComponents[sel[0]]
            aeNames = [aeName for aeName, aeOutline in atomicElements]
            self.w.atomicElementList.set(aeNames)
        else:
            self.w.atomicElementList.set([])
        self.w.atomicElementList.setSelection([])

    def atomicElementListSelectionChangedCallback(self, sender):
        self.drawCurrentGlyph()
        self.displayDrawing()

    def axisSliderCallback(self, sender):
        self.updateCurrentGlyph()
        self.drawCurrentGlyph()
        self.displayDrawing()

    def drawCurrentGlyph(self):
        db.newDrawing()
        db.translate(100, 100)
        db.scale(0.8)
        db.fill(None)
        db.stroke(0.2, 0.3, 1)
        db.rect(0, 0, 1000, 1000)
        db.stroke(None)
        db.translate(0, 120)  # Baseline at 120 from the bottom of the Ideographic Em Square
        db.fill(0, 1, 0, 0.3)
        db.stroke(0)
        dcSelection = set(self.w.deepComponentList.getSelection())
        aeSelection = set(self.w.atomicElementList.getSelection())
        if self._currentGlyphOutline is not None:
            drawOutline(self._currentGlyphOutline)
        if self._currentGlyphComponents:
            for dcIndex, (dcName, atomicElements) in enumerate(self._currentGlyphComponents):
                for aeIndex, (aeName, atomicOutline) in enumerate(atomicElements):
                    if dcIndex in dcSelection:
                        if aeIndex in aeSelection:
                            db.fill(1, 0, 0, 0.3)
                        else:
                            db.fill(0, 0, 1, 0.3)
                    else:
                        db.fill(0, 0.3)
                    drawOutline(atomicOutline)
        db.endDrawing()

    def displayDrawing(self):
        context = DrawBotContext()
        _drawBotDrawingTool._drawInContext(context)
        self.w.dbView.setPDFDocument(context.getNSPDFDocument())


def drawOutline(outline):
    bez = db.BezierPath()
    outline.drawPoints(bez)
    db.drawPath(bez)


if __name__ == "__main__":
    from vanilla.dialogs import getFolder
    result = getFolder("Please select a .rcjk project folder")
    if result:
        RoboCJKPreviewer(result[0])
