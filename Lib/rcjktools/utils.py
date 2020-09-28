import math
from fontTools.misc.transform import Transform


def makeTransform(x, y, rotation, scalex, scaley, rcenterx, rcentery, scaleUsesCenter=False):
    rotation = math.radians(rotation)
    if not scaleUsesCenter:
        rcenterx *= scalex
        rcentery *= scaley
        t = Transform()
        t = t.translate(x + rcenterx, y + rcentery)
        t = t.rotate(rotation)
        t = t.translate(-rcenterx, -rcentery)
        t = t.scale(scalex, scaley)
    else:
        t = Transform()
        t = t.translate(x + rcenterx, y + rcentery)
        t = t.rotate(rotation)
        t = t.scale(scalex, scaley)
        t = t.translate(-rcenterx, -rcentery)
    return t


def recenterTransform(x, y, rotation, scalex, scaley, rcenterx, rcentery, newrcenterx, newrcentery):
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


def convertOffsetFromRCenterToTCenter(x, y, rotation, scalex, scaley, rcenterx, rcentery):
    """Take a set of transformation parameters that use a center only for rotation
    ("rcenter"), and return the new x, y offset for the equivalent transform that
    uses a center for rotation and scaling ("tcenter"), so that

        t1 = makeTransform(x, y, rotation, scalex, scaley, rcenterx, rcentery)
        t2 = makeTransform(newx, newy, rotation, scalex, scaley, rcenterx, rcentery, scaleUsesCenter=True)

    return the same transformation (bar floating point rounding errors).
    """
    t = makeTransform(x, y, rotation, scalex, scaley, rcenterx, rcentery)
    tmp = makeTransform(x, y, rotation, scalex, scaley, rcenterx, rcentery, scaleUsesCenter=True)
    newx = x + t[4] - tmp[4]
    newy = y + t[5] - tmp[5]
    return newx, newy
