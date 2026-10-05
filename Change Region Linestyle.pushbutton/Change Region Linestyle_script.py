# -*- coding: utf-8 -*-

__title__ = "Change Region\nLinestyle"
__doc__ = """
Change the boundary line style of selected Filled Regions
and Masking Regions.

Uses Revit's FilledRegion boundary line-style API directly.
Regions are modified in place and are not recreated.
"""

__author__ = "Aaron Rumple"
__copyright__ = "Copyright 2025, Aaron Rumple"
__credits__ = ["Aaron Rumple"]
__license__ = "GPL"
__version__ = "2025.10.05"
__maintainer__ = "Aaron Rumple"
__email__ = "aaronrumple@gmail.com"
__status__ = "Production"


from Autodesk.Revit.DB import (
    FilledRegion,
    Transaction,
    SubTransaction
)

from Autodesk.Revit.UI.Selection import (
    ISelectionFilter,
    ObjectType
)

from pyrevit import revit, forms, script


doc = revit.doc
uidoc = revit.uidoc
output = script.get_output()


# ================================================================
# SELECTION FILTER
# ================================================================

class RegionSelectionFilter(ISelectionFilter):

    def AllowElement(self, element):
        return isinstance(element, FilledRegion)

    def AllowReference(self, reference, point):
        return False


# ================================================================
# GET REGIONS
# ================================================================

def get_regions():
    """
    Use preselected Filled/Masking Regions when available.

    If none are preselected, prompt the user.
    """

    regions = []

    # ------------------------------------------------------------
    # Preselection
    # ------------------------------------------------------------

    try:
        for element_id in uidoc.Selection.GetElementIds():

            element = doc.GetElement(element_id)

            if isinstance(element, FilledRegion):
                regions.append(element)

    except:
        pass


    if regions:
        return regions


    # ------------------------------------------------------------
    # Interactive selection
    # ------------------------------------------------------------

    try:

        references = uidoc.Selection.PickObjects(
            ObjectType.Element,
            RegionSelectionFilter(),
            "Select Filled Regions or Masking Regions"
        )

    except:
        return []


    for reference in references:

        element = doc.GetElement(
            reference.ElementId
        )

        if isinstance(element, FilledRegion):
            regions.append(element)


    return regions


# ================================================================
# ELEMENT ID VALUE
# ================================================================

def id_value(element_id):
    """
    Return ElementId numeric value across Revit versions.
    """

    try:
        return element_id.Value
    except:
        try:
            return element_id.IntegerValue
        except:
            return None


# ================================================================
# VALID LINE STYLES
# ================================================================

def get_valid_line_styles(region):
    """
    Return the GraphicsStyle elements Revit permits for
    Filled/Masking Region boundaries.

    Uses the API confirmed to work for both region types.
    """

    styles = []

    try:

        style_ids = region.GetValidLineStyleIdsForFilledRegion(
            doc
        )

    except:
        return styles


    for style_id in style_ids:

        try:

            style = doc.GetElement(
                style_id
            )

            if style is not None:
                styles.append(style)

        except:
            pass


    styles.sort(
        key=lambda x: x.Name.lower()
    )


    return styles


# ================================================================
# COMMON VALID LINE STYLES
# ================================================================

def get_common_line_styles(regions):
    """
    Return line styles valid for every selected region.

    Normally all selected regions return the same valid style list,
    but intersecting them ensures the dialog only offers styles that
    Revit accepts for every selected region.
    """

    if not regions:
        return []


    first_styles = get_valid_line_styles(
        regions[0]
    )


    if not first_styles:
        return []


    # ------------------------------------------------------------
    # Initial dictionary
    # ------------------------------------------------------------

    common = {}


    for style in first_styles:

        key = id_value(
            style.Id
        )

        if key is not None:
            common[key] = style


    # ------------------------------------------------------------
    # Intersect remaining region style lists
    # ------------------------------------------------------------

    for region in regions[1:]:

        valid_ids = set()


        for style in get_valid_line_styles(region):

            key = id_value(
                style.Id
            )

            if key is not None:
                valid_ids.add(key)


        for key in list(common.keys()):

            if key not in valid_ids:
                del common[key]


    result = list(
        common.values()
    )


    result.sort(
        key=lambda x: x.Name.lower()
    )


    return result


# ================================================================
# LINE STYLE OPTION
# ================================================================

class LineStyleOption(object):

    def __init__(self, graphics_style):

        self.graphics_style = graphics_style
        self.name = graphics_style.Name


    def __str__(self):
        return self.name


# ================================================================
# SELECT LINE STYLE
# ================================================================

def select_line_style(styles):

    options = [
        LineStyleOption(style)
        for style in styles
    ]


    selected = forms.SelectFromList.show(
        options,
        name_attr="name",
        title="Change Region Linestyle",
        button_name="Change Linestyle",
        multiselect=False,
        width=500,
        height=600
    )


    if selected is None:
        return None


    return selected.graphics_style


# ================================================================
# CHANGE REGIONS
# ================================================================

def change_regions(
    regions,
    selected_style
):
    """
    Set the boundary line style for each selected region.

    A SubTransaction is used for each region so one failure does
    not prevent other regions from being processed.
    """

    changed = []
    failed = []


    transaction = Transaction(
        doc,
        "Change Region Linestyle"
    )


    transaction.Start()


    try:

        for region in regions:

            subtransaction = SubTransaction(
                doc
            )


            subtransaction.Start()


            try:

                # ================================================
                # CONFIRMED WORKING REVIT API
                #
                # Works for:
                #   - Filled Regions
                #   - Masking Regions
                #
                # No sketch manipulation is required.
                # ================================================

                region.SetLineStyleId(
                    selected_style.Id
                )


                subtransaction.Commit()


                changed.append(
                    region.Id
                )


            except Exception as ex:

                try:
                    subtransaction.RollBack()
                except:
                    pass


                failed.append([
                    region.Id,
                    str(ex)
                ])


        transaction.Commit()


    except:

        try:
            transaction.RollBack()
        except:
            pass

        raise


    return changed, failed


# ================================================================
# REPORT
# ================================================================

def print_report(
    regions,
    selected_style,
    changed,
    failed
):

    output.print_md(
        "# Change Region Linestyle"
    )


    output.print_md(
        "**Line Style:** `{}`  \n"
        "**Selected Regions:** {}  \n"
        "**Changed:** {}  \n"
        "**Failed:** {}".format(
            selected_style.Name,
            len(regions),
            len(changed),
            len(failed)
        )
    )


    # ------------------------------------------------------------
    # Changed
    # ------------------------------------------------------------

    if changed:

        rows = []


        for region_id in changed:

            rows.append([
                output.linkify(region_id),
                selected_style.Name
            ])


        output.print_md(
            "### Changed Regions"
        )


        output.print_table(
            table_data=rows,
            columns=[
                "ElementId",
                "Line Style"
            ]
        )


    # ------------------------------------------------------------
    # Failed
    # ------------------------------------------------------------

    if failed:

        rows = []


        for region_id, reason in failed:

            rows.append([
                output.linkify(region_id),
                str(reason)
            ])


        output.print_md(
            "### Failed"
        )


        output.print_table(
            table_data=rows,
            columns=[
                "ElementId",
                "Reason"
            ]
        )


# ================================================================
# MAIN
# ================================================================

def main():

    # ------------------------------------------------------------
    # Get Filled / Masking Regions
    # ------------------------------------------------------------

    regions = get_regions()


    if not regions:

        forms.alert(
            "Select one or more Filled Regions or Masking Regions.",
            title="Change Region Linestyle"
        )

        return


    # ------------------------------------------------------------
    # Determine line styles valid for all selected regions
    # ------------------------------------------------------------

    line_styles = get_common_line_styles(
        regions
    )


    if not line_styles:

        forms.alert(
            "No common valid boundary line styles were found "
            "for the selected regions.",
            title="Change Region Linestyle"
        )

        return


    # ------------------------------------------------------------
    # Select line style
    # ------------------------------------------------------------

    selected_style = select_line_style(
        line_styles
    )


    if selected_style is None:
        return


    # ------------------------------------------------------------
    # Change regions
    # ------------------------------------------------------------

    try:

        changed, failed = change_regions(
            regions,
            selected_style
        )

    except Exception as ex:

        forms.alert(
            "The transaction could not be completed.\n\n{}".format(
                str(ex)
            ),
            title="Change Region Linestyle"
        )

        return


    # ------------------------------------------------------------
    # Report
    # ------------------------------------------------------------

    print_report(
        regions,
        selected_style,
        changed,
        failed
    )


# ================================================================
# RUN
# ================================================================

if __name__ == "__main__":
    main()