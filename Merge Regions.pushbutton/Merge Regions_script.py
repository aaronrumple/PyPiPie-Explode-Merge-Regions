# -*- coding: utf-8 -*-
"""
Merge Regions - pyRevit / IronPython 2.7
Revit 2022-2027

Merges selected Filled Regions and Masking Regions into one FilledRegion element.
Overlaps are unioned using temporary Revit solids. Nested filled islands are
kept as separate positive solids until final boundary recovery, preventing them
from being swallowed by a Boolean union with the surrounding ring.
"""

__title__ = "Merge Regions"
__author__ = "Aaron Rumple, AIA"

import clr
import System
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')
clr.AddReference('System')

from System import Array
from System.Collections.Generic import List
from System.Windows import (Window, Thickness, HorizontalAlignment, VerticalAlignment,
    TextWrapping, WindowStartupLocation, ResizeMode)
from System.Windows.Controls import (Grid, RowDefinition, ColumnDefinition, TextBlock,
    ComboBox, ComboBoxItem, CheckBox, Button, GroupBox, StackPanel, Orientation)
from System.Windows import GridLength, GridUnitType

from Autodesk.Revit.DB import (
    BuiltInCategory, BuiltInParameter, BooleanOperationsType, BooleanOperationsUtils, CurveLoop,
    Element, ElementId, FilledRegion, FilledRegionType, FilteredElementCollector,
    GeometryCreationUtilities, GraphicsStyle, Options, PlanarFace, Solid, XYZ,
    Transaction, ElementTransformUtils
)
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType

from pyrevit import revit, forms, script


doc = revit.doc
uidoc = revit.uidoc
view = doc.ActiveView
output = script.get_output()

EPS = 1.0e-7
EXTRUDE = 0.01  # feet; temporary only
VOL_EPS = 1.0e-10


# -----------------------------------------------------------------------------
# Selection
# -----------------------------------------------------------------------------
class RegionFilter(ISelectionFilter):
    def AllowElement(self, e):
        return isinstance(e, FilledRegion)
    def AllowReference(self, ref, point):
        return False


def selected_regions():
    found = []
    for eid in uidoc.Selection.GetElementIds():
        e = doc.GetElement(eid)
        if isinstance(e, FilledRegion) and e.OwnerViewId == view.Id:
            found.append(e)
    if len(found) >= 2:
        return found
    try:
        refs = uidoc.Selection.PickObjects(ObjectType.Element, RegionFilter(),
                                           "Select two or more filled/masking regions")
        return [doc.GetElement(r.ElementId) for r in refs]
    except:
        return []


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def ename(e):
    try:
        return e.Name
    except:
        try:
            return e.get_Parameter(BuiltInCategory.INVALID).AsString()
        except:
            return str(e.Id.IntegerValue)


def type_name(t):
    """Return the displayed Revit type name, never the ElementId."""
    try:
        p = t.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
        if p:
            name = p.AsString()
            if name:
                return name
    except:
        pass
    try:
        name = Element.Name.GetValue(t)
        if name:
            return name
    except:
        pass
    try:
        name = t.Name
        if name:
            return name
    except:
        pass
    return "<Unnamed Filled Region Type>"


def is_masking(region):
    try:
        return bool(region.IsMasking)
    except:
        t = doc.GetElement(region.GetTypeId())
        try:
            return bool(t.IsMasking)
        except:
            return False


def get_filled_types(masking):
    vals = []
    for t in FilteredElementCollector(doc).OfClass(FilledRegionType):
        try:
            if bool(t.IsMasking) == masking:
                vals.append(t)
        except:
            if not masking:
                vals.append(t)
    vals.sort(key=lambda x: type_name(x).lower())
    return vals


def get_line_styles():
    vals = []
    try:
        ids = FilledRegion.GetValidLineStyleIdsForFilledRegion(doc)
        for eid in ids:
            e = doc.GetElement(eid)
            if e:
                vals.append(e)
    except:
        # Older API fallback: Lines category subcategories.
        try:
            cat = doc.Settings.Categories.get_Item(BuiltInCategory.OST_Lines)
            for sub in cat.SubCategories:
                vals.append(sub.GetGraphicsStyle(0))
        except:
            pass
    vals.sort(key=lambda x: getattr(x, 'Name', '').lower())
    return vals


def clone_loop(loop):
    nl = CurveLoop()
    for c in loop:
        nl.Append(c.Clone())
    return nl


def loop_list(loops):
    result = List[CurveLoop]()
    for lp in loops:
        result.Add(clone_loop(lp))
    return result


def _loop_points(loop):
    """Return tessellated XYZ points for containment testing."""
    pts = []
    for c in loop:
        try:
            segpts = list(c.Tessellate())
        except:
            segpts = [c.GetEndPoint(0), c.GetEndPoint(1)]
        for p in segpts:
            if not pts or p.DistanceTo(pts[-1]) > EPS:
                pts.append(p)
    if len(pts) > 1 and pts[0].DistanceTo(pts[-1]) < EPS:
        pts.pop()
    return pts


def _uv(p):
    # Filled-region boundaries lie in the active view plane.  Project to the
    # view's local Right/Up axes so this also works in sections/elevations.
    o = view.Origin
    v = p - o
    return (v.DotProduct(view.RightDirection), v.DotProduct(view.UpDirection))


def _point_in_loop(point, loop):
    pts = _loop_points(loop)
    if len(pts) < 3:
        return False
    x, y = _uv(point)
    poly = [_uv(p) for p in pts]
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)):
            den = (yj - yi)
            if abs(den) > EPS:
                xhit = (xj - xi) * (y - yi) / den + xi
                if x < xhit:
                    inside = not inside
        j = i
    return inside


def _loop_test_point(loop):
    pts = _loop_points(loop)
    if not pts:
        raise Exception("A region contains an empty boundary loop.")
    # Boundary loops from a valid FilledRegion do not cross one another, so a
    # vertex is sufficient for determining containment by another loop.
    return pts[0]


def _extrude_one(loop):
    one = List[CurveLoop]()
    one.Add(clone_loop(loop))
    return GeometryCreationUtilities.CreateExtrusionGeometry(one, view.ViewDirection.Normalize(), EXTRUDE)


def region_to_solids(region):
    """
    Convert one region to solids using even/odd nesting parity.

    depth 0 = filled outer boundary
    depth 1 = hole
    depth 2 = filled island
    depth 3 = hole in island, etc.

    Each even-depth loop becomes a positive solid and only its immediate
    odd-depth children are subtracted.  This is the key difference from
    extruding every boundary loop at once, which can lose nested islands.
    """
    loops = [clone_loop(lp) for lp in region.GetBoundaries()]
    if not loops:
        return []

    parents = [-1] * len(loops)
    depths = [0] * len(loops)
    samples = [_loop_test_point(lp) for lp in loops]

    # Parent = smallest containing loop.  Use containment count to establish
    # depth, then find the containing loop one level above.
    contains = []
    for i in range(len(loops)):
        c = []
        for j in range(len(loops)):
            if i != j and _point_in_loop(samples[i], loops[j]):
                c.append(j)
        contains.append(c)
        depths[i] = len(c)

    for i in range(len(loops)):
        target_depth = depths[i] - 1
        if target_depth >= 0:
            for j in contains[i]:
                if depths[j] == target_depth:
                    parents[i] = j
                    break

    solids = []
    for i, lp in enumerate(loops):
        if depths[i] % 2 != 0:
            continue
        solid = _extrude_one(lp)
        # Subtract only direct hole children. Nested islands are emitted as
        # their own positive solids and later participate in the global union.
        for h in range(len(loops)):
            if parents[h] == i and depths[h] == depths[i] + 1:
                hole = _extrude_one(loops[h])
                solid = BooleanOperationsUtils.ExecuteBooleanOperation(
                    solid, hole, BooleanOperationsType.Difference)
        solids.append(solid)
    return solids


def solid_bbox(s):
    """Bounding box in the active view's local Right/Up coordinates."""
    pts = []
    for f in s.Faces:
        try:
            mesh = f.Triangulate()
            for i in range(mesh.NumVertices):
                pts.append(mesh.get_Vertex(i))
        except:
            pass
    if not pts:
        return None
    uv = [_uv(p) for p in pts]
    return (min(p[0] for p in uv), min(p[1] for p in uv),
            max(p[0] for p in uv), max(p[1] for p in uv))


def boxes_touch(a, b):
    if a is None or b is None:
        return True
    return not (a[2] < b[0]-EPS or b[2] < a[0]-EPS or
                a[3] < b[1]-EPS or b[3] < a[1]-EPS)


def solids_really_overlap(a, b):
    """
    True only when the two filled solids have positive common volume.

    This test is critical for nested islands.  An island lies inside the outer
    ring's bounding box, but it is separated from that ring by a hole.  Revit's
    Boolean Union must NOT be called for that disjoint pair or the island can be
    lost.  Actual overlapping regions, on the other hand, have positive
    intersection volume and are unioned.
    """
    if not boxes_touch(solid_bbox(a), solid_bbox(b)):
        return False
    try:
        inter = BooleanOperationsUtils.ExecuteBooleanOperation(
            a, b, BooleanOperationsType.Intersect)
        return inter is not None and inter.Volume > VOL_EPS
    except:
        return False


def union_solids(solids):
    """
    Union only solids that ACTUALLY overlap.

    Disjoint components and nested positive islands deliberately remain as
    separate solids.  Their top-face loops are all passed to FilledRegion.Create,
    which lets Revit classify the complete set of non-intersecting boundaries.
    """
    work = list(solids)
    changed = True
    while changed:
        changed = False
        i = 0
        while i < len(work):
            j = i + 1
            while j < len(work):
                if not solids_really_overlap(work[i], work[j]):
                    j += 1
                    continue
                try:
                    u = BooleanOperationsUtils.ExecuteBooleanOperation(
                        work[i], work[j], BooleanOperationsType.Union)
                    work[i] = u
                    del work[j]
                    changed = True
                except:
                    j += 1
            i += 1
    return work

def top_face(solid):
    """Return the planar cap face furthest along the active view normal."""
    normal = view.ViewDirection.Normalize()
    best = None
    best_d = -1.0e100
    for f in solid.Faces:
        pf = f if isinstance(f, PlanarFace) else None
        if pf is None:
            continue
        try:
            n = pf.FaceNormal.Normalize()
            if n.DotProduct(normal) > 0.999:
                d = pf.Origin.DotProduct(normal)
                if d > best_d:
                    best_d = d
                    best = pf
        except:
            pass
    return best


def result_loops(solids):
    """
    Recover EVERY cap-face loop from EVERY surviving solid.

    A ring and its nested island intentionally arrive here as separate solids;
    both sets of loops are retained.  This is what v5 failed to guarantee.
    """
    loops = []
    normal = view.ViewDirection.Normalize()
    tr = Autodesk.Revit.DB.Transform.CreateTranslation(normal * (-EXTRUDE))
    for s in solids:
        f = top_face(s)
        if f is None:
            raise Exception("Could not recover a planar union face.")
        for lp in f.GetEdgesAsCurveLoops():
            nl = CurveLoop()
            for c in lp:
                nl.Append(c.CreateTransformed(tr))
            loops.append(nl)
    return loops


# Import Transform without wildcarding the DB namespace above.
import Autodesk.Revit.DB


def create_result(kind_masking, result_type, loops):
    iloops = loop_list(loops)
    major = int(doc.Application.VersionNumber)
    if kind_masking:
        # Revit 2024+ has the explicit method. For 2022/2023, FilledRegion.Create
        # with a masking FilledRegionType is attempted; this works in project docs
        # where the type is valid, and fails cleanly otherwise.
        if major >= 2024 and hasattr(FilledRegion, 'CreateMaskingRegion'):
            return FilledRegion.CreateMaskingRegion(doc, view.Id, iloops)
        return FilledRegion.Create(doc, result_type.Id, view.Id, iloops)
    return FilledRegion.Create(doc, result_type.Id, view.Id, iloops)


def source_edge_styles(regions):
    """Collect visible source geometry edges and their GraphicsStyleIds."""
    data = []
    opt = Options()
    opt.View = view
    opt.IncludeNonVisibleObjects = True
    for r in regions:
        try:
            ge = r.get_Geometry(opt)
            for obj in ge:
                if isinstance(obj, Solid):
                    for edge in obj.Edges:
                        c = edge.AsCurve()
                        sid = edge.GraphicsStyleId
                        if sid and sid != ElementId.InvalidElementId:
                            data.append((c, sid))
        except:
            pass
    return data


def curve_mid(c):
    try:
        return c.Evaluate(0.5, True)
    except:
        p0 = c.GetEndPoint(0); p1 = c.GetEndPoint(1)
        return (p0 + p1) * 0.5


def dist_point_curve(p, c):
    try:
        pr = c.Project(p)
        return pr.Distance if pr else 1.0e100
    except:
        return 1.0e100


def keep_boundary_styles(new_region, src_styles):
    """Best-effort preservation for surviving source edges."""
    if not src_styles:
        return 0
    changed = 0
    try:
        from Autodesk.Revit.DB import CurveElementFilter, CurveElementType
        ids = new_region.GetDependentElements(CurveElementFilter(CurveElementType.DetailCurve))
        for eid in ids:
            ce = doc.GetElement(eid)
            try:
                c = ce.GeometryCurve
                p = curve_mid(c)
                best = None; best_d = 1.0e100
                for sc, sid in src_styles:
                    d = dist_point_curve(p, sc)
                    if d < best_d:
                        best_d = d; best = sid
                # Tight tolerance: only copy when output segment lies on a source edge.
                if best is not None and best_d < 1.0e-5:
                    gs = doc.GetElement(best)
                    if gs:
                        ce.LineStyle = gs
                        changed += 1
            except:
                pass
        if changed:
            # Force region graphics to refresh after sketch-line style edits.
            delta = XYZ(1.0e-5, 0, 0)
            ElementTransformUtils.MoveElement(doc, new_region.Id, delta)
            ElementTransformUtils.MoveElement(doc, new_region.Id, -delta)
    except:
        pass
    return changed


# -----------------------------------------------------------------------------
# WPF dialog
# -----------------------------------------------------------------------------
class MergeDialog(Window):
    def __init__(self, regions):
        self.regions = regions
        self.filled_types = get_filled_types(False)
        self.masking_types = get_filled_types(True)
        self.line_styles = get_line_styles()
        kinds = set([is_masking(r) for r in regions])
        self.mixed = len(kinds) > 1

        # "Keep Current Filled Region Type" is available only when every selected
        # region is a filled region and every region has the same FilledRegionType.
        self.common_filled_type = None
        if regions and all([not is_masking(r) for r in regions]):
            type_ids = set([r.GetTypeId().IntegerValue for r in regions])
            if len(type_ids) == 1:
                self.common_filled_type = doc.GetElement(regions[0].GetTypeId())

        self.Title = "Merge Regions"
        self.Width = 540
        self.Height = 470
        self.MinWidth = self.Width
        self.MaxWidth = self.Width
        self.MinHeight = self.Height
        self.MaxHeight = self.Height
        self.SizeToContent = System.Windows.SizeToContent.Manual
        self.WindowStartupLocation = WindowStartupLocation.CenterScreen
        self.ResizeMode = ResizeMode.NoResize

        root = Grid(); root.Margin = Thickness(16)
        for h in [34, 78, 76, 72, 72, 50]:
            rd = RowDefinition(); rd.Height = GridLength(h); root.RowDefinitions.Add(rd)
        self.Content = root

        summary = TextBlock()
        nf = len([r for r in regions if not is_masking(r)])
        nm = len(regions) - nf
        summary.Text = "%d selected region(s): %d filled, %d masking" % (len(regions), nf, nm)
        summary.VerticalAlignment = VerticalAlignment.Center
        Grid.SetRow(summary, 0); root.Children.Add(summary)

        gb1 = GroupBox(); gb1.Header = "Result"; Grid.SetRow(gb1, 1); root.Children.Add(gb1)
        g1 = Grid(); g1.Margin = Thickness(8); gb1.Content = g1
        for _ in range(2): g1.ColumnDefinitions.Add(ColumnDefinition())
        self.kind = ComboBox(); self.kind.Margin = Thickness(0,0,8,0)
        self.kind.Items.Add("Filled Region"); self.kind.Items.Add("Masking Region")
        if self.mixed:
            self.kind.SelectedIndex = 0
        else:
            self.kind.SelectedIndex = 1 if list(kinds)[0] else 0
        self.kind.SelectionChanged += self.kind_changed
        Grid.SetColumn(self.kind, 0); g1.Children.Add(self.kind)
        self.rtype = ComboBox(); Grid.SetColumn(self.rtype, 1); g1.Children.Add(self.rtype)

        gb2 = GroupBox(); gb2.Header = "Original regions"; Grid.SetRow(gb2, 2); root.Children.Add(gb2)
        sp2 = StackPanel(); sp2.Orientation = Orientation.Horizontal; sp2.Margin = Thickness(8); gb2.Content = sp2
        self.keep = CheckBox(); self.keep.Content = "Keep originals"; self.keep.IsChecked = False
        sp2.Children.Add(self.keep)

        gb3 = GroupBox(); gb3.Header = "Boundary line styles"; Grid.SetRow(gb3, 3); root.Children.Add(gb3)
        g3 = Grid(); g3.Margin = Thickness(8,6,8,6)
        c0 = ColumnDefinition(); c0.Width = GridLength(230)
        c1 = ColumnDefinition(); c1.Width = GridLength(1, GridUnitType.Star)
        g3.ColumnDefinitions.Add(c0); g3.ColumnDefinitions.Add(c1)
        gb3.Content = g3
        self.replace = CheckBox(); self.replace.Content = "Replace all boundary line styles"
        self.replace.IsChecked = False; self.replace.Checked += self.style_toggle; self.replace.Unchecked += self.style_toggle
        Grid.SetColumn(self.replace, 0); g3.Children.Add(self.replace)
        self.lstyle = ComboBox(); self.lstyle.IsEnabled = False; self.lstyle.Height = 26; self.lstyle.VerticalAlignment = VerticalAlignment.Center
        for s in self.line_styles:
            item = ComboBoxItem(); item.Content = getattr(s, 'Name', str(s.Id.IntegerValue)); item.Tag = s
            self.lstyle.Items.Add(item)
        if self.lstyle.Items.Count: self.lstyle.SelectedIndex = 0
        Grid.SetColumn(self.lstyle, 1); g3.Children.Add(self.lstyle)

        note = TextBlock(); note.TextWrapping = TextWrapping.Wrap
        note.Text = ("Keep line styles preserves surviving source-edge styles where Revit exposes them. "
                     "Edges created by the union use Revit's resulting/default boundary style. "
                     "Replace applies one selected style to every resulting boundary.")
        note.Margin = Thickness(2,6,2,2); note.VerticalAlignment = VerticalAlignment.Top; Grid.SetRow(note, 4); root.Children.Add(note)

        buttons = StackPanel(); buttons.Orientation = Orientation.Horizontal; buttons.HorizontalAlignment = HorizontalAlignment.Right; buttons.VerticalAlignment = VerticalAlignment.Center
        ok = Button(); ok.Content = "Merge"; ok.Width = 90; ok.Height = 28; ok.Margin = Thickness(6); ok.IsDefault = True; ok.Click += self.ok
        cancel = Button(); cancel.Content = "Cancel"; cancel.Width = 90; cancel.Height = 28; cancel.Margin = Thickness(6); cancel.IsCancel = True
        buttons.Children.Add(ok); buttons.Children.Add(cancel); Grid.SetRow(buttons, 5); root.Children.Add(buttons)
        self.kind_changed(None, None)

    def kind_changed(self, sender, args):
        if not hasattr(self, 'rtype'): return
        self.rtype.Items.Clear()
        vals = self.masking_types if self.kind.SelectedIndex == 1 else self.filled_types

        # Default option when all selected inputs are filled regions of one type.
        if self.kind.SelectedIndex == 0 and self.common_filled_type is not None:
            item = ComboBoxItem()
            item.Content = "Keep Current Filled Region Type"
            item.Tag = self.common_filled_type
            self.rtype.Items.Add(item)

        for t in vals:
            item = ComboBoxItem(); item.Content = type_name(t); item.Tag = t; self.rtype.Items.Add(item)
        if self.rtype.Items.Count: self.rtype.SelectedIndex = 0
        # Explicit masking Create does not need a type in 2024+, but showing types keeps
        # behavior consistent and provides the 2022/23 fallback type.
        self.rtype.IsEnabled = self.rtype.Items.Count > 0

    def style_toggle(self, sender, args):
        self.lstyle.IsEnabled = bool(self.replace.IsChecked)

    def ok(self, sender, args):
        if self.kind.SelectedIndex < 0:
            forms.alert("Choose the resulting region kind.") ; return
        if self.rtype.SelectedItem is None:
            forms.alert("No compatible region type is available in this project.") ; return
        if self.replace.IsChecked and self.lstyle.SelectedItem is None:
            forms.alert("Choose a replacement line style.") ; return
        self.DialogResult = True
        self.Close()


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
regions = selected_regions()
if len(regions) < 2:
    forms.alert("Select at least two Filled Regions and/or Masking Regions in the active view.", exitscript=True)

# Capture source styles before any originals are deleted.
src_styles = source_edge_styles(regions)

dlg = MergeDialog(regions)
if dlg.ShowDialog() != True:
    script.exit()

kind_masking = dlg.kind.SelectedIndex == 1
result_type = dlg.rtype.SelectedItem.Tag
keep_originals = bool(dlg.keep.IsChecked)
replace_styles = bool(dlg.replace.IsChecked)
replacement_style = dlg.lstyle.SelectedItem.Tag if replace_styles else None

# Geometry work is outside the transaction because all solids are temporary.
try:
    solids = []
    for r in regions:
        solids.extend(region_to_solids(r))
    merged_solids = union_solids(solids)
    loops = result_loops(merged_solids)
except Exception as ex:
    forms.alert("Could not merge the selected region geometry.\n\n%s" % ex, exitscript=True)

new_region = None
kept_style_count = 0
t = Transaction(doc, "Merge Regions")
try:
    t.Start()
    new_region = create_result(kind_masking, result_type, loops)

    # In older Revit versions the fallback Create uses the selected type.
    # In 2024+ explicit masking creation chooses the masking behavior directly.
    if (not kind_masking) and new_region.GetTypeId() != result_type.Id:
        try: new_region.ChangeTypeId(result_type.Id)
        except: pass

    if replace_styles:
        new_region.SetLineStyleId(replacement_style.Id)
    else:
        doc.Regenerate()
        kept_style_count = keep_boundary_styles(new_region, src_styles)

    if not keep_originals:
        for r in regions:
            doc.Delete(r.Id)

    t.Commit()
except Exception as ex:
    if t.HasStarted():
        t.RollBack()
    forms.alert("Merge Regions failed. No changes were made.\n\n%s" % ex, exitscript=True)

try:
    uidoc.Selection.SetElementIds(List[ElementId]([new_region.Id]))
except:
    pass

output.print_md("## Merge Regions")
output.print_md("- **Input regions:** %d" % len(regions))
output.print_md("- **Merged filled components:** %d" % len(merged_solids))
output.print_md("- **Result boundary loops:** %d" % len(loops))
output.print_md("- **Result:** %s" % ("Masking Region" if kind_masking else "Filled Region"))
output.print_md("- **Result type:** %s" % type_name(result_type))
output.print_md("- **Originals:** %s" % ("kept" if keep_originals else "deleted"))
if replace_styles:
    output.print_md("- **Boundary style:** replaced with `%s`" % getattr(replacement_style, 'Name', 'selected style'))
else:
    output.print_md("- **Boundary styles:** preserved on %d surviving sketch segment(s) where matched" % kept_style_count)
output.print_md("- **Output ElementId:** %s" % output.linkify(new_region.Id))
