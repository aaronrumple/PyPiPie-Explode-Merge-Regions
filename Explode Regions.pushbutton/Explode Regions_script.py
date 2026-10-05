# -*- coding: utf-8 -*-
__title__ = "Explode\nRegions"
__doc__ = "Explode selected Filled/Masking Regions into disconnected islands while preserving holes, nested islands, type, and boundary line styles. Styles are mapped from actual dependent sketch curves."

from pyrevit import revit, DB, UI, forms, script
from System.Collections.Generic import List
from System.Windows import (Window, WindowStartupLocation, Thickness, HorizontalAlignment,
                           ResizeMode, GridLength, GridUnitType, TextWrapping)
from System.Windows.Controls import (Grid, RowDefinition, ColumnDefinition, TextBlock,
                                    RadioButton, Button, StackPanel, Orientation)
import math

uidoc = revit.uidoc
doc = revit.doc
output = script.get_output()

TOL = 1.0e-6
MATCH_TOL = 1.0e-3  # 1/64 in approx; style matching only


def idval(eid):
    try: return eid.Value
    except: return eid.IntegerValue


def xyz2(p, view=None):
    """Project XYZ into the owning view plane; supports plans, sections, elevations."""
    if view is not None:
        try:
            return (p.DotProduct(view.RightDirection), p.DotProduct(view.UpDirection))
        except:
            pass
    return (p.X, p.Y)

def dist2(a, b):
    dx = a[0]-b[0]; dy = a[1]-b[1]
    return dx*dx + dy*dy


def tessellate_loop(loop, view=None):
    pts = []
    for c in loop:
        try: cp = list(c.Tessellate())
        except: cp = [c.GetEndPoint(0), c.GetEndPoint(1)]
        if not cp: continue
        for p in cp[:-1]:
            q = xyz2(p, view)
            if not pts or dist2(q, pts[-1]) > TOL*TOL:
                pts.append(q)
    if loop:
        try:
            q = xyz2(list(loop)[-1].GetEndPoint(1), view)
            if not pts or dist2(q, pts[-1]) > TOL*TOL: pts.append(q)
        except: pass
    if len(pts) > 1 and dist2(pts[0], pts[-1]) <= TOL*TOL: pts.pop()
    return pts


def signed_area(poly):
    if len(poly) < 3: return 0.0
    s = 0.0
    for i, p in enumerate(poly):
        q = poly[(i+1) % len(poly)]
        s += p[0]*q[1] - q[0]*p[1]
    return 0.5*s


def point_on_segment(p, a, b, tol=MATCH_TOL):
    vx=b[0]-a[0]; vy=b[1]-a[1]; wx=p[0]-a[0]; wy=p[1]-a[1]
    cross=abs(vx*wy-vy*wx)
    L=math.sqrt(vx*vx+vy*vy)
    if L < tol: return dist2(p,a) <= tol*tol
    if cross/L > tol: return False
    dot=wx*vx+wy*vy
    return -tol <= dot <= vx*vx+vy*vy+tol


def point_in_poly(p, poly):
    # Boundary counts as inside; nesting sample points are nudged away from boundaries below.
    if len(poly) < 3: return False
    inside = False
    j = len(poly)-1
    for i in range(len(poly)):
        a=poly[j]; b=poly[i]
        if point_on_segment(p,a,b): return True
        if ((a[1] > p[1]) != (b[1] > p[1])):
            x = (b[0]-a[0])*(p[1]-a[1])/(b[1]-a[1]) + a[0]
            if p[0] < x: inside = not inside
        j=i
    return inside


def interior_point(poly):
    # Polygon centroid first; otherwise midpoint of an edge nudged toward bbox center.
    A = signed_area(poly)
    if abs(A) > TOL:
        cx=cy=0.0
        for i,p in enumerate(poly):
            q=poly[(i+1)%len(poly)]; cross=p[0]*q[1]-q[0]*p[1]
            cx += (p[0]+q[0])*cross; cy += (p[1]+q[1])*cross
        c=(cx/(6.0*A), cy/(6.0*A))
        if point_in_poly(c, poly): return c
    minx=min(p[0] for p in poly); maxx=max(p[0] for p in poly)
    miny=min(p[1] for p in poly); maxy=max(p[1] for p in poly)
    center=((minx+maxx)/2.0,(miny+maxy)/2.0)
    for i,a in enumerate(poly):
        b=poly[(i+1)%len(poly)]; m=((a[0]+b[0])/2.0,(a[1]+b[1])/2.0)
        for f in (1e-5,1e-4,1e-3,1e-2,0.05):
            p=(m[0]+(center[0]-m[0])*f, m[1]+(center[1]-m[1])*f)
            if point_in_poly(p,poly): return p
    return poly[0]


def analyze_loops(boundaries, view=None):
    data=[]
    for idx, loop in enumerate(boundaries):
        poly=tessellate_loop(loop, view)
        if len(poly)<3 or abs(signed_area(poly))<TOL: continue
        data.append({'idx':idx,'loop':loop,'poly':poly,'area':abs(signed_area(poly))})
    # Determine each loop's immediate parent: smallest larger loop containing it.
    for d in data:
        p=interior_point(d['poly']); containers=[]
        for o in data:
            if o is d or o['area'] <= d['area'] + TOL: continue
            if point_in_poly(p,o['poly']): containers.append(o)
        d['parent'] = min(containers,key=lambda x:x['area']) if containers else None
    def depth(d):
        n=0; p=d['parent']; guard=0
        while p is not None and guard < len(data):
            n+=1; p=p['parent']; guard+=1
        return n
    for d in data: d['depth']=depth(d)
    # Every even-depth loop is a solid island. Its direct odd-depth children are holes.
    groups=[]
    for d in sorted(data,key=lambda x:(x['depth'],-x['area'])):
        if d['depth'] % 2 == 0:
            holes=[h for h in data if h['parent'] is d and h['depth']==d['depth']+1]
            groups.append([d]+sorted(holes,key=lambda x:-x['area']))
    return groups


def get_sketch_curves(region):
    curves=[]
    try:
        filt=DB.CurveElementFilter(DB.CurveElementType.DetailCurve)
        ids=region.GetDependentElements(filt)
        for eid in ids:
            e=doc.GetElement(eid)
            if isinstance(e, DB.CurveElement): curves.append(e)
    except: pass
    # Family/model cases can expose model curves instead.
    if not curves:
        try:
            for eid in region.GetDependentElements(None):
                e=doc.GetElement(eid)
                if isinstance(e, DB.CurveElement): curves.append(e)
        except: pass
    return curves


def curve_midpoint(curve):
    try:
        return curve.Evaluate(0.5, True)
    except:
        try:
            return (curve.GetEndPoint(0) + curve.GetEndPoint(1)) * 0.5
        except:
            return None


def point_curve_distance(curve, point):
    """3D distance from point to a bounded Revit curve."""
    if curve is None or point is None:
        return None
    try:
        result = curve.Project(point)
        if result is None:
            return None
        # Project() can project to an unbounded extension for some curve types.
        # Confirm the projected point lies on the bounded curve when possible.
        try:
            p = result.XYZPoint
            p0 = curve.GetEndPoint(0)
            p1 = curve.GetEndPoint(1)
            chord = p0.DistanceTo(p1)
            if chord > TOL:
                if p.DistanceTo(p0) > curve.Length + MATCH_TOL:
                    return None
                if p.DistanceTo(p1) > curve.Length + MATCH_TOL:
                    return None
        except:
            pass
        return result.Distance
    except:
        return None


def best_curve_at_point(point, records, tol=MATCH_TOL):
    best = None
    best_dist = None
    for rec in records:
        d = point_curve_distance(rec['curve'], point)
        if d is None or d > tol:
            continue
        if best_dist is None or d < best_dist:
            best = rec
            best_dist = d
    return best


def source_sketch_records(region):
    """Return actual source sketch geometry + LineStyle IDs."""
    records = []
    for ce in get_sketch_curves(region):
        try:
            records.append({
                'curve': ce.GeometryCurve.Clone(),
                'style_id': ce.LineStyle.Id,
                'element_id': ce.Id
            })
        except:
            pass
    return records


def capture_loop_styles(region, boundaries):
    """Attach actual sketch-curve styles to each source boundary loop.

    The source CurveLoop geometry defines topology. Each boundary segment gets
    its style from the dependent sketch curve passing through its midpoint.
    Endpoint/length equality is deliberately NOT used.
    """
    sketch = source_sketch_records(region)
    loop_styles = {}
    unmatched = 0

    for loop_index, loop in enumerate(boundaries):
        records = []
        for boundary_curve in loop:
            c = boundary_curve.Clone()
            hit = best_curve_at_point(curve_midpoint(c), sketch)
            if hit is None:
                unmatched += 1
                records.append({'curve': c, 'style_id': None})
            else:
                records.append({'curve': c, 'style_id': hit['style_id']})
        loop_styles[loop_index] = records

    return loop_styles, unmatched


def restore_group_styles(region, group, loop_styles):
    """Style every NEW sketch curve from only this island's source loops.

    Matching is NEW -> SOURCE. Therefore every new curve is considered. A
    split/reversed/reparameterized curve still matches because its midpoint
    only needs to lie on one of the source loop curves. Full circles/ellipses
    are independent of their parameter start point for the same reason.
    """
    source = []
    for loop_data in group:
        source.extend(loop_styles.get(loop_data['idx'], []))

    styled = 0
    unmatched = 0
    details = []

    for ce in get_sketch_curves(region):
        try:
            new_curve = ce.GeometryCurve
            mid = curve_midpoint(new_curve)
            hit = best_curve_at_point(mid, source)
            if hit is None or hit.get('style_id') is None:
                unmatched += 1
                details.append(ce.Id)
                continue

            style = doc.GetElement(hit['style_id'])
            if style is None:
                unmatched += 1
                details.append(ce.Id)
                continue

            ce.LineStyle = style
            styled += 1
        except:
            unmatched += 1
            try:
                details.append(ce.Id)
            except:
                pass

    return styled, unmatched, details

def refresh_region(region):
    try:
        delta=DB.XYZ(1.0e-5,0,0)
        DB.ElementTransformUtils.MoveElement(doc,region.Id,delta)
        DB.ElementTransformUtils.MoveElement(doc,region.Id,-delta)
    except: pass


def is_masking(region):
    # IMPORTANT: masking is an INSTANCE property of FilledRegion.
    # Do not infer this from FilledRegionType parameters. A normal filled
    # region can otherwise be incorrectly recreated as a masking region.
    try:
        return bool(region.IsMasking)
    except:
        return False


def create_region(src, loops):
    netloops = List[DB.CurveLoop]()
    for x in loops:
        netloops.Add(x['loop'])

    view_id = src.OwnerViewId

    # Preserve the actual source region kind.
    # Masking regions have no FilledRegionType argument when created.
    if is_masking(src):
        return DB.FilledRegion.CreateMaskingRegion(doc, view_id, netloops)

    # A filled region is recreated with the EXACT source type so its
    # foreground/background fill patterns, colors, masking setting, etc.
    # remain the same as the original FilledRegionType.
    type_id = src.GetTypeId()
    if type_id == DB.ElementId.InvalidElementId:
        raise Exception('Source filled region has no valid FilledRegionType.')
    return DB.FilledRegion.Create(doc, type_id, view_id, netloops)

# Selection: preselection first, otherwise pick regions.
selected=[]
for eid in uidoc.Selection.GetElementIds():
    e=doc.GetElement(eid)
    if isinstance(e,DB.FilledRegion): selected.append(e)
if not selected:
    try:
        refs=uidoc.Selection.PickObjects(UI.Selection.ObjectType.Element,"Select filled or masking regions")
        selected=[doc.GetElement(r.ElementId) for r in refs if isinstance(doc.GetElement(r.ElementId),DB.FilledRegion)]
    except: script.exit()
if not selected:
    forms.alert("No filled or masking regions selected.",title=__title__); script.exit()

class ExplodeOptionsWindow(Window):
    def __init__(self, count):
        self.Title = "Explode Regions"
        self.Width = 430
        self.Height = 220
        self.MinWidth = 390
        self.MinHeight = 200
        self.ResizeMode = ResizeMode.CanResize
        self.WindowStartupLocation = WindowStartupLocation.CenterScreen
        self.result = None

        root = Grid()
        root.Margin = Thickness(18)
        root.RowDefinitions.Add(RowDefinition(Height=GridLength.Auto))
        root.RowDefinitions.Add(RowDefinition(Height=GridLength.Auto))
        root.RowDefinitions.Add(RowDefinition(Height=GridLength.Auto))
        root.RowDefinitions.Add(RowDefinition(Height=GridLength(1.0, GridUnitType.Star)))
        root.RowDefinitions.Add(RowDefinition(Height=GridLength.Auto))

        heading = TextBlock()
        heading.Text = "Explode {} selected region(s)".format(count)
        heading.FontSize = 16
        heading.FontWeight = __import__('System').Windows.FontWeights.SemiBold
        heading.Margin = Thickness(0, 0, 0, 6)
        Grid.SetRow(heading, 0)
        root.Children.Add(heading)

        note = TextBlock()
        note.Text = "Separate disconnected islands while preserving holes, region type, and boundary line styles."
        note.TextWrapping = TextWrapping.Wrap
        note.Margin = Thickness(0, 0, 0, 14)
        Grid.SetRow(note, 1)
        root.Children.Add(note)

        opts = StackPanel()
        opts.Margin = Thickness(4, 0, 0, 0)
        self.rb_keep = RadioButton()
        self.rb_keep.Content = "Keep original regions"
        self.rb_keep.IsChecked = True
        self.rb_keep.Margin = Thickness(0, 0, 0, 8)
        opts.Children.Add(self.rb_keep)
        self.rb_delete = RadioButton()
        self.rb_delete.Content = "Delete original regions"
        opts.Children.Add(self.rb_delete)
        Grid.SetRow(opts, 2)
        root.Children.Add(opts)

        buttons = StackPanel()
        buttons.Orientation = Orientation.Horizontal
        buttons.HorizontalAlignment = HorizontalAlignment.Right
        buttons.Margin = Thickness(0, 16, 0, 0)

        ok = Button()
        ok.Content = "OK"
        ok.Width = 86
        ok.Height = 28
        ok.Margin = Thickness(0, 0, 8, 0)
        ok.IsDefault = True
        ok.Click += self._ok
        buttons.Children.Add(ok)

        cancel = Button()
        cancel.Content = "Cancel"
        cancel.Width = 86
        cancel.Height = 28
        cancel.IsCancel = True
        cancel.Click += self._cancel
        buttons.Children.Add(cancel)

        Grid.SetRow(buttons, 4)
        root.Children.Add(buttons)
        self.Content = root

    def _ok(self, sender, args):
        self.result = bool(self.rb_delete.IsChecked)
        self.DialogResult = True
        self.Close()

    def _cancel(self, sender, args):
        self.result = None
        self.DialogResult = False
        self.Close()


dlg = ExplodeOptionsWindow(len(selected))
dlg.ShowDialog()
if dlg.result is None:
    script.exit()
delete_originals = dlg.result

created=[]; failed=[]; unchanged=[]; styled=0; style_unmatched=0

# IMPORTANT: boundary-style changes and the geometry-refresh nudge are done in
# separate committed transactions. Revit does not reliably update the displayed
# boundary graphics when dependent sketch-curve styles are changed and the
# region is nudged inside the same transaction.
tg=DB.TransactionGroup(doc,"Explode Filled and Masking Regions")
tg.Start()
try:
    work=[]

    # 1. Capture source geometry/styles and create all replacement regions.
    t_create=DB.Transaction(doc,"Create Exploded Regions")
    t_create.Start()
    for src in selected:
        try:
            boundaries=list(src.GetBoundaries())
            owner_view=doc.GetElement(src.OwnerViewId)
            groups=analyze_loops(boundaries, owner_view)
            if len(groups)<=1:
                unchanged.append(src.Id)
                continue

            # Capture styles BEFORE creating/deleting anything.
            loop_styles, source_unmatched=capture_loop_styles(src,boundaries)
            new_for_src=[]
            for group in groups:
                nr=create_region(src,group)
                new_for_src.append((nr,group))
                created.append(nr.Id)
            work.append((src,new_for_src,loop_styles,source_unmatched))
        except Exception as ex:
            failed.append((src.Id,str(ex)))
    t_create.Commit()

    # 2. Restore boundary styles after the new regions actually exist in the DB.
    #    Do not nudge in this transaction.
    t_style=DB.Transaction(doc,"Restore Region Boundary Styles")
    t_style.Start()
    for src,new_for_src,loop_styles,source_unmatched in work:
        style_unmatched += source_unmatched
        for nr,group in new_for_src:
            try:
                nstyled, nunmatched, unmatched_ids = restore_group_styles(nr,group,loop_styles)
                styled += nstyled
                style_unmatched += nunmatched
                if unmatched_ids:
                    failed.append((nr.Id, "Unmatched new boundary curve(s): {}".format(len(unmatched_ids))))
            except Exception as ex:
                failed.append((nr.Id,"Boundary style: " + str(ex)))
    t_style.Commit()

    # 3. A NEW transaction is required for the nudge. This forces Revit to
    #    rebuild the visible FilledRegion/MaskingRegion edge graphics from the
    #    already-committed sketch line styles.
    t_refresh=DB.Transaction(doc,"Refresh Region Boundary Graphics")
    t_refresh.Start()
    for src,new_for_src,loop_styles,source_unmatched in work:
        for nr,group in new_for_src:
            refresh_region(nr)
    t_refresh.Commit()

    # 4. Delete originals only after style restoration and refresh are complete.
    if delete_originals:
        t_delete=DB.Transaction(doc,"Delete Original Regions")
        t_delete.Start()
        for src,new_for_src,loop_styles,source_unmatched in work:
            try:
                if src.IsValidObject:
                    doc.Delete(src.Id)
            except Exception as ex:
                failed.append((src.Id,"Delete original: " + str(ex)))
        t_delete.Commit()

    tg.Assimilate()
except:
    try: tg.RollBack()
    except: pass
    raise

output.print_md("# Explode Regions")
output.print_md("**Selected:** {}  \\n**New regions:** {}  \\n**Unchanged (already one island):** {}  \\n**Failed:** {}  \\n**Originals:** {}  \\n**Boundary sketch styles restored:** {}  \
**Unmatched boundary styles:** {}".format(
    len(selected),len(created),len(unchanged),len(failed),"Deleted" if delete_originals else "Kept",styled,style_unmatched))
if created:
    output.print_md("## Created")
    for eid in created: output.print_md("- {}".format(output.linkify(eid)))
if unchanged:
    output.print_md("## Unchanged")
    for eid in unchanged: output.print_md("- {} — one solid island".format(output.linkify(eid)))
if failed:
    output.print_md("## Failed")
    for eid,msg in failed: output.print_md("- {} — `{}`".format(output.linkify(eid),msg.replace('`',"'")))
