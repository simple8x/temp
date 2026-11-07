"""Conservative geometric predicates; intentionally do not create electrical nets."""
from math import hypot

def near(a,b,tol=0.25):
    return hypot(a[0]-b[0],a[1]-b[1]) <= tol

def axis_aligned_intersection(a,b,c,d,tol=0.25):
    """Return intersection point for orthogonal segments, else None."""
    ax,ay=a; bx,by=b; cx,cy=c; dx,dy=d
    ah=abs(ay-by)<=tol; av=abs(ax-bx)<=tol
    ch=abs(cy-dy)<=tol; cv=abs(cx-dx)<=tol
    if ah and cv:
        x,y=cx,ay
        if min(ax,bx)-tol<=x<=max(ax,bx)+tol and min(cy,dy)-tol<=y<=max(cy,dy)+tol: return (x,y)
    if av and ch:
        x,y=ax,cy
        if min(cx,dx)-tol<=x<=max(cx,dx)+tol and min(ay,by)-tol<=y<=max(ay,by)+tol: return (x,y)
    return None

def electrical_join_allowed(intersection, explicit_junction=False, shared_endpoint=False):
    """A geometric crossing alone never establishes electrical connectivity."""
    return bool(explicit_junction or shared_endpoint)
