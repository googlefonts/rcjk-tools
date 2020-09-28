import operator
from typing import NamedTuple
from fontTools.misc.transform import Transform
from fontTools.pens.pointPen import PointToSegmentPen, SegmentToPointPen
from fontTools.pens.recordingPen import RecordingPointPen
from fontTools.ufoLib.glifLib import readGlyphFromString


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
    def loadFromGLIF(cls, p, scaleUsesCenter=False):
        with open(p) as f:
            data = f.read()
        self = cls(scaleUsesCenter=scaleUsesCenter)
        readGlyphFromString(data, self, self.getPointPen())
        return self

    def __init__(self, scaleUsesCenter=False):
        self._scaleUsesCenter = scaleUsesCenter  # temporary switch
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
        result = Glyph(scaleUsesCenter=self._scaleUsesCenter)
        result.name = self.name
        result.unicodes = self.unicodes
        result.width = op(self.width, scalar)
        result.outline = op(self.outline, scalar)
        result.components = [op(compo, scalar) for compo in self.components]
        return result

    def _doBinaryOperator(self, other, op):
        result = Glyph(scaleUsesCenter=self._scaleUsesCenter)
        result.name = self.name
        result.unicodes = self.unicodes
        result.width = op(self.width, other.width)
        result.outline = op(self.outline, other.outline)
        result.components = [
            op(compo1, compo2)
            for compo1, compo2 in zip(self.components, other.components)
        ]
        return result


def normalizeLocation(location, axes):
    location = {axisName: normalizeValue(v, *axes.get(axisName, (0, 1)))
                for axisName, v in location.items()}
    return _clampLocation(location)


def normalizeValue(value, minValue, maxValue):
    assert minValue < maxValue
    return (value - minValue) / (maxValue - minValue)


def _clampLocation(d):
    return {k: min(1, max(0, v)) for k, v in d.items()}


class Component(NamedTuple):

    name: str
    coord: dict
    transform: dict

    def __add__(self, other):
        return Component(
            self.name,
            self.coord + other.coord,
            self.transform + other.transform,
        )

    def __sub__(self, other):
        return Component(
            self.name,
            self.coord - other.coord,
            self.transform - other.transform,
        )

    def __mul__(self, scalar):
        return Component(
            self.name,
            self.coord * scalar,
            self.transform * scalar,
        )

    def __rmul__(self, scalar):
        return self.__mul__(scalar)


class MathDict(dict, _MathMixin):

    def _doBinaryOperatorScalar(self, scalar, op):
        result = MathDict()
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
        result = MathDict()
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