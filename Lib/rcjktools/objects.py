import logging
import operator
from typing import NamedTuple
from fontTools.misc.transform import Transform
from fontTools.pens.filterPen import FilterPointPen
from fontTools.pens.pointPen import PointToSegmentPen, SegmentToPointPen
from fontTools.pens.recordingPen import RecordingPointPen
from fontTools.ufoLib.glifLib import readGlyphFromString
from fontTools.varLib.models import normalizeValue


logger = logging.getLogger(__name__)


class InterpolationError(Exception):
    pass


class _MathMixin:
    def __add__(self, other):
        return self._doBinaryOperator(other, operator.add)

    def __sub__(self, other):
        return self._doBinaryOperator(other, operator.sub)

    def __mul__(self, scalar):
        return self._doBinaryOperatorScalar(scalar, operator.mul)

    def __rmul__(self, scalar):
        return self._doBinaryOperatorScalar(scalar, operator.mul)


class Glyph(_MathMixin):
    @classmethod
    def loadFromGLIF(cls, glifPath):
        with open(glifPath) as f:
            data = f.read()
        self = cls()
        try:
            readGlyphFromString(data, self, self.getPointPen())
        except Exception:
            logger.error(f"failed to load .glif file: {glifPath}")
            raise
        return self

    @classmethod
    def loadFromGlyphObject(cls, glyphObject):
        # glyphObject is a ufoLib2 Glyph object (or defcon)
        self = cls()
        self.name = glyphObject.name
        glyphObject.drawPoints(self.getPointPen())
        self.width = glyphObject.width
        self.unicodes = glyphObject.unicodes
        self.lib = dict(glyphObject.lib)
        return self

    def __init__(self):
        self.name = None
        self.width = 0
        self.unicodes = []
        self.outline = MathOutline()
        self.components = []
        self.lib = {}
        self.location = {}  # neutral
        self.axes = {}
        self.variations = []
        self.model = None
        self.deltas = None
        self._ensuredComponentCoords = False

    def ensureComponentCoords(self, glyphSet):
        if self._ensuredComponentCoords:
            return

        for compoIndex, compo in enumerate(self.components):
            if compo.name not in glyphSet:
                # classic component
                continue

            allAxisNames = {
                axisName
                for g in [self] + self.variations
                for axisName in g.components[compoIndex].coord
            }
            compoGlyph = glyphSet.getGlyph(compo.name)
            allAxisNames &= set(compoGlyph.axes)
            for axisName in sorted(allAxisNames):
                defaultValue = compoGlyph.axes[axisName][1]
                axisValues = [g.components[compoIndex].coord.get(axisName) for g in [self] + self.variations]
                if None in axisValues:
                    if axisValues[0] is None:
                        if any(v is not None and v != defaultValue for v in axisValues):
                            print("---", self.name, compoIndex, compo.name, axisName, axisValues)
                        # FIX default source only
                        assert axisName not in compo.coord
                        compo.coord[axisName] = defaultValue

        self._ensuredComponentCoords = True

    def getPointPen(self):
        return self.outline

    def getPen(self):
        return SegmentToPointPen(self.outline)

    def drawPoints(self, pen):
        self.outline.drawPoints(pen)

    def draw(self, pen):
        self.outline.draw(pen)

    def instantiate(self, location):
        if self.model is None:
            return self  # XXX raise error?
        if self.deltas is None:
            self.deltas = self.model.getDeltas([self] + self.variations)
        location = normalizeLocation(location, self.axes)
        return self.model.interpolateFromDeltas(location, self.deltas)

    def _doBinaryOperatorScalar(self, scalar, op):
        result = self.__class__()
        result.name = self.name
        result.unicodes = self.unicodes
        result.width = op(self.width, scalar)
        result.outline = op(self.outline, scalar)
        result.components = [op(compo, scalar) for compo in self.components]
        return result

    def _doBinaryOperator(self, other, op):
        result = self.__class__()
        result.name = self.name
        result.unicodes = self.unicodes
        result.width = op(self.width, other.width)
        result.outline = op(self.outline, other.outline)
        # if len(self.components) != len(other.components):
        #     raise InterpolationError("incompatible number of components")
        result.components = [
            op(compo1, compo2)
            for compo1, compo2 in zip(self.components, other.components)
        ]
        return result


def normalizeLocation(location, axes):
    """This behaves different from varLib.models.normalizeLocation in that
    it won't add missing axes values and doesn't filter values that aren't
    in the axes dict.
    """
    return {
        axisName: normalizeValue(v, axes.get(axisName, (-1, 0, 1)))
        for axisName, v in location.items()
    }


class Component(NamedTuple):

    name: str
    coord: dict
    transform: dict

    def __add__(self, other):
        # if self.name != other.name:
        #     raise InterpolationError("incompatible component")
        return Component(
            self.name, self.coord + other.coord, self.transform + other.transform
        )

    def __sub__(self, other):
        # if self.name != other.name:
        #     raise InterpolationError("incompatible component")
        return Component(
            self.name, self.coord - other.coord, self.transform - other.transform
        )

    def __mul__(self, scalar):
        return Component(self.name, self.coord * scalar, self.transform * scalar)

    def __rmul__(self, scalar):
        return self.__mul__(scalar)


class MathDict(dict, _MathMixin):
    def _doBinaryOperatorScalar(self, scalar, op):
        result = self.__class__()
        for k, v in self.items():
            if isinstance(v, (int, float)):
                result[k] = op(v, scalar)
            else:
                result[k] = v
        return result

    def _doBinaryOperator(self, other, op):
        # any missing keys will be taken from the other dict
        self_other = dict(other)
        self_other.update(self)
        other_self = dict(self)
        other_self.update(other)
        result = self.__class__()
        for k, v1 in self_other.items():
            v2 = other_self[k]
            if isinstance(v1, (int, float)):
                result[k] = op(v1, v2)
            else:
                if v1 != v2:
                    raise InterpolationError("incompatible dicts")
                result[k] = v1
        return result


class MathOutline(RecordingPointPen, _MathMixin):
    def isEmpty(self):
        return not self.value

    def drawPoints(self, pen):
        self.replay(pen)

    def draw(self, pen):
        self.drawPoints(PointToSegmentPen(pen))

    def transform(self, t):
        if not hasattr(t, "transformPoint"):
            t = Transform(*t)
        return self.applyUnaryFunc(t.transformPoint)

    def applyUnaryFunc(self, func):
        result = MathOutline()
        for m, args, kwargs in self.value:
            if m == "addPoint":
                pt, seg, smooth, name = args
                result.addPoint(func(pt), seg, smooth, name, **kwargs)
            elif m == "beginPath":
                result.beginPath()
            elif m == "endPath":
                result.endPath()
            else:
                assert False, f"unsupported method: {m}"
        return result

    def _doBinaryOperatorScalar(self, scalar, op):
        def func(pt):
            x, y = pt
            return op(x, scalar), op(y, scalar)

        return self.applyUnaryFunc(func)

    def _doBinaryOperator(self, other, op):
        result = MathOutline()
        if len(self.value) != len(other.value):
            raise InterpolationError("incompatible outline")

        for (m1, args1, kwargs1), (m2, args2, kwargs2) in zip(self.value, other.value):
            if m1 != m2:
                raise InterpolationError("incompatible outline")
            if m1 == "addPoint":
                (x1, y1), seg1, smooth1, name1 = args1
                (x2, y2), seg2, smooth2, name2 = args2
                if seg1 != seg2:
                    raise InterpolationError("incompatible outline")
                pt = op(x1, x2), op(y1, y2)
                result.addPoint(pt, seg1, smooth1, name1, **kwargs1)
            elif m1 == "beginPath":
                result.beginPath()
            elif m1 == "endPath":
                result.endPath()
            else:
                assert False, f"unsupported method: {m1}"
        return result

    def splitComponents(self):
        """Separate outlines from components; return a new MathOutline object that
        does not contain components, and a list of (baseGlyphName, transformation)
        tuples representing the components.
        """
        outline = MathOutline()
        cc = ComponentCollector(outline)
        self.drawPoints(cc)
        return outline, cc.components


class ComponentCollector(FilterPointPen):

    """This pen passes all outline data on to the outPen, and
    stores component data in a list.
    """

    def __init__(self, outPen):
        super().__init__(outPen)
        self.components = []

    def addComponent(self, glyphName, transformation, **kwargs):
        self.components.append((glyphName, transformation))
