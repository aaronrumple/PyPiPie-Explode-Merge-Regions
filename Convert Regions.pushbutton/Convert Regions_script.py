# -*- coding: utf-8 -*-
__title__ = "Convert Region"
__doc__ = "Convert Filled Regions <-> Masking Regions with mixed-selection support, Filled type prompting, and preserved mixed boundary line styles."

from pyrevit import revit, DB, UI, forms, script
from System.Collections.Generic import List

uidoc = revit.uidoc
doc = revit.doc
output = script.get_output()

STYLE_TOL = 1e-5  # feet


class RegionSelectionFilter(UI.Selection.ISelectionFilter):
    def AllowElement(self, element):
        return isinstance(element, DB.FilledRegion)

    def AllowReference(self, reference, point):
        return False


def get_id_value(element_or_id):
    """Revit 2024+ ElementId.Value with IntegerValue fallback."""
    try:
        return element_or_id.Id.Value
    except Exception:
        try:
            return element_or_id.Id.IntegerValue
        except Exception:
            try:
                return element_or_id.Value
            except Exception:
                return element_or_id.IntegerValue


def is_valid_element(element):
    try:
        return bool(element is not None and element.IsValidObject)
    except Exception:
        return element is not None


def classify_region(region):
    """Return True only for an actual Masking Region element.

    FilledRegion.IsMasking identifies the INSTANCE kind (Filled Region vs
    Masking Region). FilledRegionType.IsMasking is a different property: it
    controls whether a filled-region TYPE obscures geometry behind it and must
    not be used to classify the element.
    """
    try:
        return bool(region.IsMasking)
    except Exception as ex:
        raise Exception("Could not read FilledRegion.IsMasking: {}".format(ex))


def get_regions():
    """Use valid preselection; otherwise force a fresh pick."""
    regions = []
    invalid_or_stale = False

    for element_id in uidoc.Selection.GetElementIds():
        try:
            element = doc.GetElement(element_id)
            if is_valid_element(element) and isinstance(element, DB.FilledRegion):
                regions.append(element)
            else:
                invalid_or_stale = True
        except Exception:
            invalid_or_stale = True

    if regions:
        return regions

    if invalid_or_stale:
        try:
            uidoc.Selection.SetElementIds(List[DB.ElementId]())
        except Exception:
            pass

    try:
        refs = uidoc.Selection.PickObjects(
            UI.Selection.ObjectType.Element,
            RegionSelectionFilter(),
            "Select Filled or Masking Region(s) to Convert"
        )
    except Exception:
        return []

    picked = []
    for reference in refs:
        try:
            element = doc.GetElement(reference.ElementId)
            if is_valid_element(element) and isinstance(element, DB.FilledRegion):
                picked.append(element)
        except Exception:
            pass
    return picked


def element_name(element):
    try:
        return DB.Element.Name.GetValue(element)
    except Exception:
        try:
            param = element.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM)
            if param:
                value = param.AsString()
                if value:
                    return value
        except Exception:
            pass
    return "Element {}".format(get_id_value(element))


class FilledRegionTypeOption(forms.TemplateListItem):
    @property
    def name(self):
        return element_name(self.item)


def get_filled_region_types():
    """Return all FilledRegionType elements.

    FilledRegionType.IsMasking only controls whether a FILLED REGION TYPE
    masks geometry behind it. It does not make instances of that type into
    Masking Region elements, so it must not be filtered here.
    """
    result = []
    for region_type in DB.FilteredElementCollector(doc).OfClass(DB.FilledRegionType):
        try:
            if is_valid_element(region_type):
                result.append(region_type)
        except Exception:
            pass
    return sorted(result, key=lambda x: element_name(x).lower())


def choose_filled_region_type():
    region_types = get_filled_region_types()
    if not region_types:
        forms.alert("No Filled Region types were found in this project.", title="Convert Region")
        return None

    selected = forms.SelectFromList.show(
        [FilledRegionTypeOption(x) for x in region_types],
        title="Select Filled Region Type",
        button_name="Convert",
        multiselect=False,
        width=420,
        height=430
    )
    if selected is None:
        return None

    if isinstance(selected, DB.FilledRegionType):
        return selected

    try:
        return selected.item
    except Exception:
        forms.alert("Could not resolve the selected Filled Region Type.", title="Convert Region")
        return None


def clone_boundaries(region):
    loops = List[DB.CurveLoop]()
    for source_loop in region.GetBoundaries():
        new_loop = DB.CurveLoop()
        for curve in source_loop:
            new_loop.Append(curve.Clone())
        loops.Add(new_loop)
    return loops


def curve_signature(curve):
    """Endpoints + length, order-independent, for matching equivalent boundary lines."""
    p0 = curve.GetEndPoint(0)
    p1 = curve.GetEndPoint(1)
    a = (p0.X, p0.Y, p0.Z)
    b = (p1.X, p1.Y, p1.Z)
    if b < a:
        a, b = b, a
    return (
        a[0], a[1], a[2],
        b[0], b[1], b[2],
        curve.Length
    )


def signature_distance(sig_a, sig_b):
    return min(
        ((sig_a[0] - sig_b[0]) ** 2 + (sig_a[1] - sig_b[1]) ** 2 + (sig_a[2] - sig_b[2]) ** 2) ** 0.5
        + ((sig_a[3] - sig_b[3]) ** 2 + (sig_a[4] - sig_b[4]) ** 2 + (sig_a[5] - sig_b[5]) ** 2) ** 0.5,
        1e99
    ) + abs(sig_a[6] - sig_b[6])


def get_boundary_detail_lines(region):
    """Get the actual dependent DetailCurve objects, not the read-only geometry copies."""
    lines = []
    try:
        ids = region.GetDependentElements(
            DB.CurveElementFilter(DB.CurveElementType.DetailCurve)
        )
    except Exception:
        ids = []

    for element_id in ids:
        try:
            element = doc.GetElement(element_id)
            if isinstance(element, DB.DetailCurve) and is_valid_element(element):
                lines.append(element)
        except Exception:
            pass
    return lines


def capture_boundary_styles(region):
    """Capture the actual style of every boundary DetailCurve before conversion."""
    records = []
    lines = get_boundary_detail_lines(region)

    for line in lines:
        try:
            records.append((curve_signature(line.GeometryCurve), line.LineStyle.Id))
        except Exception:
            pass

    return records


def count_boundary_segments(region):
    count = 0
    for loop in region.GetBoundaries():
        for curve in loop:
            count += 1
    return count


def snapshot_region(region):
    masking = classify_region(region)
    if masking is None:
        raise Exception("Could not determine whether the source is Filled or Masking.")

    styles = capture_boundary_styles(region)
    segment_count = count_boundary_segments(region)

    # Do not silently throw away mixed styles. If Revit did not expose all
    # boundary DetailCurves, stop the conversion instead of degrading the region.
    if segment_count != len(styles):
        raise Exception(
            "Could not read all boundary line styles ({} of {} segments)."
            .format(len(styles), segment_count)
        )

    return {
        "id": get_id_value(region),
        "view_id": region.OwnerViewId,
        "boundaries": clone_boundaries(region),
        "styles": styles,
        "pinned": bool(region.Pinned),
        "masking": masking,
        # Freeze conversion intent at snapshot time. Never recompute direction
        # from a newly-created region or a shared batch state.
        "direction": "Masking -> Filled" if masking else "Filled -> Masking",
    }


def create_replacement(snapshot, filled_type):
    """Create the exact opposite kind of region."""
    if snapshot["masking"]:
        # MASKING -> FILLED
        if filled_type is None:
            raise Exception("A Filled Region type is required for Masking -> Filled.")
        new_region = DB.FilledRegion.Create(
            doc,
            filled_type.Id,
            snapshot["view_id"],
            snapshot["boundaries"]
        )
    else:
        # FILLED -> MASKING
        create_masking = getattr(DB.FilledRegion, "CreateMaskingRegion", None)
        if create_masking is None:
            raise Exception("FilledRegion.CreateMaskingRegion() is not available in this Revit API.")
        new_region = create_masking(
            doc,
            snapshot["view_id"],
            snapshot["boundaries"]
        )

    if new_region is None:
        raise Exception("Revit did not create the replacement region.")

    # Hard safety check: never allow a same-kind replacement to proceed.
    expected_masking = not snapshot["masking"]
    actual_masking = classify_region(new_region)
    if actual_masking != expected_masking:
        raise Exception(
            "Wrong region kind returned. Expected {}, got {}."
            .format(
                "Masking" if expected_masking else "Filled",
                "Masking" if actual_masking else "Filled"
            )
        )

    return new_region


def match_detail_lines(region):
    """Match target DetailCurves to source signatures."""
    targets = []
    for line in get_boundary_detail_lines(region):
        try:
            targets.append((curve_signature(line.GeometryCurve), line))
        except Exception:
            pass

    return targets


def restore_boundary_styles(region, style_records):
    """Restore mixed styles by setting the actual dependent DetailCurve.LineStyle."""
    targets = match_detail_lines(region)
    if len(targets) != len(style_records):
        raise Exception(
            "Replacement boundary count changed ({} target curves, {} source styles)."
            .format(len(targets), len(style_records))
        )

    unused = list(targets)
    applied = 0

    for source_sig, style_id in style_records:
        best_index = -1
        best_error = 1e99

        for index, (target_sig, target_line) in enumerate(unused):
            error = signature_distance(source_sig, target_sig)
            if error < best_error:
                best_error = error
                best_index = index

        if best_index < 0 or best_error > STYLE_TOL:
            raise Exception("Could not geometrically match a replacement boundary line to its source style.")

        target_line = unused.pop(best_index)[1]
        style_element = doc.GetElement(style_id)
        if style_element is None:
            raise Exception("Original line style ElementId {} no longer exists.".format(get_id_value(style_id)))

        try:
            target_line.LineStyle = style_element
        except Exception as ex:
            raise Exception("Could not assign original boundary line style: {}".format(ex))

        applied += 1

    return applied


def refresh_region(region):
    """Force Revit to refresh cached region boundary graphics after DetailLine edits."""
    DB.ElementTransformUtils.MoveElement(doc, region.Id, DB.XYZ.BasisX / 12.0)
    DB.ElementTransformUtils.MoveElement(doc, region.Id, -DB.XYZ.BasisX / 12.0)


def copy_properties(source_snapshot, target):
    try:
        target.Pinned = source_snapshot["pinned"]
    except Exception:
        pass


regions = get_regions()
if not regions:
    script.exit()

snapshots = []
failed = []

for region in regions:
    try:
        snapshots.append(snapshot_region(region))
    except Exception as ex:
        try:
            rid = get_id_value(region)
        except Exception:
            rid = -1
        failed.append((rid, str(ex)))

if not snapshots:
    output.print_md("# Convert Region")
    output.print_md("**Converted:** 0")
    output.print_md("**Failed:** {}".format(len(failed)))
    for element_id, message in failed:
        output.print_md("- **{}**: {}".format(element_id, message))
    script.exit()

# A Filled Region type is needed only if at least one selected source is
# explicitly frozen as Masking -> Filled. Use the same immutable direction
# field that drives creation below; do not recompute from masking/type state.
filled_target_type = None
masking_to_filled = [snap for snap in snapshots if snap["direction"] == "Masking -> Filled"]
filled_to_masking = [snap for snap in snapshots if snap["direction"] == "Filled -> Masking"]
masking_count = len(masking_to_filled)
filled_count = len(filled_to_masking)

if masking_to_filled:
    filled_target_type = choose_filled_region_type()
    if filled_target_type is None:
        script.exit()

created = []
converted = []

# Atomic conversion. Creation is intentionally split by source kind so Revit never
# has to resolve FilledRegion.Create() and CreateMaskingRegion() for a mixed batch
# inside the same transaction. Each snapshot carries its immutable direction.
tg = DB.TransactionGroup(doc, "Convert Region")
tg.Start()
try:
    # Pass 1: MASKING -> FILLED only.
    if masking_to_filled:
        tx_m2f = DB.Transaction(doc, "Create Filled Regions")
        tx_m2f.Start()
        try:
            for snapshot in masking_to_filled:
                new_region = create_replacement(snapshot, filled_target_type)
                # Re-check immediately and record only this snapshot's direction.
                if classify_region(new_region):
                    raise Exception("Masking -> Filled created a Masking Region instead of a Filled Region.")
                created.append((snapshot, new_region, snapshot["direction"]))
            tx_m2f.Commit()
        except Exception:
            tx_m2f.RollBack()
            raise

    # Pass 2: FILLED -> MASKING only.
    if filled_to_masking:
        tx_f2m = DB.Transaction(doc, "Create Masking Regions")
        tx_f2m.Start()
        try:
            for snapshot in filled_to_masking:
                new_region = create_replacement(snapshot, None)
                # Re-check immediately and record only this snapshot's direction.
                if not classify_region(new_region):
                    raise Exception("Filled -> Masking created a Filled Region instead of a Masking Region.")
                created.append((snapshot, new_region, snapshot["direction"]))
            tx_f2m.Commit()
        except Exception:
            tx_f2m.RollBack()
            raise

    # Restore every individual boundary linestyle. This must COMMIT before the
    # region graphics refresh; otherwise Revit may display default/Thin Lines.
    tx_style = DB.Transaction(doc, "Restore Region Boundary Line Styles")
    tx_style.Start()
    try:
        doc.Regenerate()
        for snapshot, new_region, direction in created:
            restore_boundary_styles(new_region, snapshot["styles"])
            copy_properties(snapshot, new_region)
        tx_style.Commit()
    except Exception:
        tx_style.RollBack()
        raise

    # Refresh in a separate transaction after linestyle edits are committed.
    tx_refresh = DB.Transaction(doc, "Refresh Region Boundary Graphics")
    tx_refresh.Start()
    try:
        for snapshot, new_region, direction in created:
            refresh_region(new_region)
        tx_refresh.Commit()
    except Exception:
        tx_refresh.RollBack()
        raise

    # Verification gets its own transaction because Document.Regenerate() is a
    # modifying API call and is forbidden outside an open transaction.
    tx_verify = DB.Transaction(doc, "Verify Converted Regions")
    tx_verify.Start()
    try:
        doc.Regenerate()

        # Confirm both conversion direction and every mixed boundary style before
        # deleting any original region.
        for snapshot, new_region, direction in created:
            actual_masking = classify_region(new_region)
            expected_masking = (direction == "Filled -> Masking")
            if actual_masking != expected_masking:
                raise Exception(
                    "Post-refresh region kind changed. Expected {}, got {} for source {}."
                    .format(
                        "Masking" if expected_masking else "Filled",
                        "Masking" if actual_masking else "Filled",
                        snapshot["id"]
                    )
                )

            targets = match_detail_lines(new_region)
            if len(targets) != len(snapshot["styles"]):
                raise Exception(
                    "Boundary style verification failed for source {} ({} target curves, {} source styles)."
                    .format(snapshot["id"], len(targets), len(snapshot["styles"]))
                )

            unused = list(targets)
            for source_sig, expected_style_id in snapshot["styles"]:
                best_index = -1
                best_error = 1e99
                for index, (target_sig, target_line) in enumerate(unused):
                    error = signature_distance(source_sig, target_sig)
                    if error < best_error:
                        best_error = error
                        best_index = index

                if best_index < 0 or best_error > STYLE_TOL:
                    raise Exception(
                        "Could not verify a replacement boundary line for source {}."
                        .format(snapshot["id"])
                    )

                target_line = unused.pop(best_index)[1]
                actual_style = target_line.LineStyle
                if actual_style is None or get_id_value(actual_style.Id) != get_id_value(expected_style_id):
                    raise Exception(
                        "Boundary linestyle verification failed for source {}. Expected {}, got {}."
                        .format(
                            snapshot["id"],
                            get_id_value(expected_style_id),
                            get_id_value(actual_style.Id) if actual_style is not None else "None"
                        )
                    )

        tx_verify.Commit()
    except Exception:
        tx_verify.RollBack()
        raise

    # Preserve original selection/report order even though the two conversion
    # directions were created in separate transactions.
    created_by_source = {}
    for snapshot, new_region, direction in created:
        created_by_source[snapshot["id"]] = (snapshot, new_region, direction)

    tx_delete = DB.Transaction(doc, "Delete Original Regions")
    tx_delete.Start()
    try:
        for snapshot in snapshots:
            item = created_by_source.get(snapshot["id"])
            if item is None:
                raise Exception("No replacement was created for source {}.".format(snapshot["id"]))
            snap, new_region, direction = item
            doc.Delete(DB.ElementId(snapshot["id"]))
            converted.append((snapshot["id"], get_id_value(new_region), direction))
        tx_delete.Commit()
    except Exception:
        tx_delete.RollBack()
        raise

    tg.Assimilate()

except Exception as ex:
    try:
        tg.RollBack()
    except Exception:
        pass
    for snapshot in snapshots:
        failed.append((snapshot["id"], str(ex)))
    converted = []


# Select the resulting current regions so a second run immediately converts
# Masking -> Filled and opens the Filled Region Type dialog.
if converted:
    try:
        new_selection = List[DB.ElementId]()
        for old_id, new_id, direction in converted:
            new_selection.Add(DB.ElementId(new_id))
        uidoc.Selection.SetElementIds(new_selection)
    except Exception:
        pass

output.print_md("# Convert Region")
output.print_md("**Selected:** {} (Filled: {}, Masking: {})".format(
    len(snapshots), filled_count, masking_count
))
output.print_md("**Converted:** {}".format(len(converted)))
output.print_md("**Failed:** {}".format(len(failed)))

if converted:
    rows = []
    for old_id, new_id, direction in converted:
        try:
            link = output.linkify(DB.ElementId(new_id), title=str(new_id))
        except Exception:
            link = str(new_id)
        rows.append([direction, str(old_id), link])

    output.print_table(
        table_data=rows,
        columns=["Conversion", "Original ElementId", "New ElementId"],
        title="Converted Regions"
    )

if failed:
    output.print_md("## Failed")
    for element_id, message in failed:
        output.print_md("- **{}**: {}".format(element_id, message))
