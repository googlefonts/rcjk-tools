import math
from fontTools.misc.transform import Transform


def makeTransform(x, y, rotation, scalex, scaley, tcenterx, tcentery):
    rotation = math.radians(rotation)
    t = Transform()
    t = t.translate(x + tcenterx, y + tcentery)
    t = t.rotate(rotation)
    t = t.scale(scalex, scaley)
    t = t.translate(-tcenterx, -tcentery)
    return t


def makeTransformVarCo(
    x, y, rotation, scalex, scaley, skewx, skewy, tcenterx, tcentery
):
    t = Transform()
    t = t.translate(x + tcenterx, y + tcentery)
    t = t.rotate(math.radians(rotation))
    t = t.scale(scalex, scaley)
    t = t.skew(math.radians(skewx), math.radians(skewy))
    t = t.translate(-tcenterx, -tcentery)
    return t


def recenterTransform(
    x, y, rotation, scalex, scaley, rcenterx, rcentery, newrcenterx, newrcentery
):
    """Take a set of transformation parameters, new values for rcenterx and rcentery, and it will
    return new values for x and y, so that

        t1 = makeTransform(x, y, rotation, scalex, scaley, rcenterx, rcentery)
        t2 = makeTransform(newx, newy, rotation, scalex, scaley, newrcenterx, newrcentery)

    return the same transformation (bar floating point rounding errors).
    """
    t = makeTransform(x, y, rotation, scalex, scaley, rcenterx, rcentery)
    tmp = makeTransform(x, y, rotation, scalex, scaley, newrcenterx, newrcentery)
    newx = x + t[4] - tmp[4]
    newy = y + t[5] - tmp[5]
    return newx, newy


def convertOffsetFromRCenterToTCenter(
    x, y, rotation, scalex, scaley, rcenterx, rcentery
):
    """Take a set of transformation parameters that use a center only for rotation
    ("rcenter"), and return the new x, y offset for the equivalent transform that
    uses a center for rotation and scaling ("tcenter"), so that

        t1 = makeTransform(x, y, rotation, scalex, scaley, rcenterx, rcentery)
        t2 = makeTransform(newx, newy, rotation, scalex, scaley, rcenterx, rcentery, scaleUsesCenter=True)

    return the same transformation (bar floating point rounding errors).
    """
    t = makeTransform(x, y, rotation, scalex, scaley, rcenterx, rcentery)
    tmp = makeTransform(
        x, y, rotation, scalex, scaley, rcenterx, rcentery, scaleUsesCenter=True
    )
    newx = x + t[4] - tmp[4]
    newy = y + t[5] - tmp[5]
    return newx, newy


def decomposeTwoByTwo(twoByTwo):
    """Decompose a 2x2 transformation matrix into components:
    - rotation
    - scalex
    - scaley
    - skewx
    - skewy
    """
    a, b, c, d = twoByTwo
    delta = a * d - b * c

    rotation = 0
    scalex = scaley = 0
    skewx = skewy = 0

    # Apply the QR-like decomposition.
    if a != 0 or b != 0:
        r = math.sqrt(a * a + b * b)
        rotation = math.acos(a / r) if b > 0 else -math.acos(a / r)
        scalex, scaley = (r, delta / r)
        skewx, skewy = (math.atan((a * c + b * d) / (r * r)), 0)
    elif c != 0 or d != 0:
        s = math.sqrt(c * c + d * d)
        rotation = math.pi / 2 - (math.acos(-c / s) if d > 0 else -math.acos(c / s))
        scalex, scaley = (delta / s, s)
        skewx, skewy = (0, math.atan((a * c + b * d) / (s * s)))
    else:
        # a = b = c = d = 0
        pass

    return rotation, scalex, scaley, skewx, skewy


def tuplifyLocation(loc):
    return tuple(sorted(loc.items()))
