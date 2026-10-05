# Merge & Explode Regions

pyRevit tools for merging and exploding **Filled Regions** and **Masking Regions** in Autodesk Revit while preserving region topology, including **holes, islands, nested islands, and disconnected areas**.

These tools are intended for cleaning up and restructuring complex detail regions without manually redrawing their boundaries.

---

## Tools

### Merge Regions

Combines multiple selected Filled Regions and/or Masking Regions into new region geometry.

The tool performs a true 2D geometric merge so overlapping regions become a single region rather than simply combining their original boundary loops.

### Explode Regions

Separates a Filled Region or Masking Region containing multiple disconnected areas into individual region elements.

Holes and nested islands remain associated with the correct resulting region.

---

# Merge Regions

## What It Does

**Merge Regions** combines selected regions in the active view.

Supported input:

- Filled Regions
- Masking Regions
- Multiple disconnected regions
- Overlapping regions
- Regions containing holes
- Regions containing nested islands

Other selected Revit elements are ignored.

---

## Example — Overlapping Regions

### Before

```text
┌─────────────┐
│             │
│       ┌─────┼───────┐
│       │     │       │
└───────┼─────┘       │
        │             │
        └─────────────┘
```

Two overlapping regions.

### After

```text
┌─────────────┐
│             │
│             └───────┐
│                     │
└───────┐             │
        │             │
        └─────────────┘
```

The overlapping geometry is unioned into **one continuous region**.

Internal boundaries caused only by the overlap are removed.

---

## Example — Hole

### Before

```text
┌─────────────────────┐
│                     │
│     ┌─────────┐     │
│     │  HOLE   │     │
│     └─────────┘     │
│                     │
└─────────────────────┘
```

### After

```text
┌─────────────────────┐
│█████████████████████│
│█████┌─────────┐█████│
│█████│  HOLE   │█████│
│█████└─────────┘█████│
│█████████████████████│
└─────────────────────┘
```

The interior opening remains a hole.

---

## Example — Island Inside a Hole

Nested boundaries are preserved.

```text
┌───────────────────────────┐
│███████████████████████████│
│████┌───────────────┐██████│
│████│               │██████│
│████│    ┌─────┐    │██████│
│████│    │█████│    │██████│
│████│    └─────┘    │██████│
│████│               │██████│
│████└───────────────┘██████│
│███████████████████████████│
└───────────────────────────┘
```

Topology is interpreted by nesting depth:

```text
Depth 0    Filled
Depth 1    Hole
Depth 2    Filled Island
Depth 3    Hole
Depth 4    Filled Island
...
```

This allows complex regions to retain their visual appearance after merging.

---

## Merge Options

The WPF dialog provides control over the resulting region.

### Output Type

Choose:

- **Filled Region**
- **Masking Region**

When Filled Region output is selected, the available Filled Region Types are displayed by **name**.

### Keep Current Filled Region Type

When all selected source regions:

1. Are Filled Regions, and
2. Use the same Filled Region Type

the default option is:

> **Keep Current Filled Region Type**

If the selected regions do not share a common Filled Region Type, a new output type must be selected.

---

## Original Regions

Choose whether to:

- **Keep Originals**
- **Delete Originals**

Deleting originals makes the operation behave like a conventional merge command.

Keeping originals allows the result to be reviewed before the source geometry is manually removed.

---

## Boundary Line Styles

Boundary line styles can either be preserved or replaced.

Options include:

- Keep source line styles
- Replace boundary line styles
- Select a project line style for the replacement boundaries

When preserving source styles, the tool attempts to transfer the appropriate style from the original boundary geometry to the resulting boundary geometry.

---

# Explode Regions

## What It Does

**Explode Regions** performs the opposite operation.

A region containing multiple disconnected filled areas is separated into individual Revit region elements.

---

## Example — Disconnected Areas

### Before

One Revit region containing three disconnected areas:

```text
┌───────┐

                ┌───────────┐
                │           │
                └───────────┘

      ┌─────┐
      │     │
      └─────┘
```

### After

```text
Region 1

┌───────┐


Region 2

┌───────────┐
│           │
└───────────┘


Region 3

┌─────┐
│     │
└─────┘
```

Each disconnected filled area becomes a separate Revit region.

---

## Exploding Regions With Holes

A hole remains associated with its containing region.

### Before

```text
┌───────────────────┐
│███████████████████│
│████┌───────┐██████│
│████│       │██████│
│████└───────┘██████│
│███████████████████│
└───────────────────┘

          ┌───────┐
          │███████│
          └───────┘
```

### After

**Region 1**

```text
┌───────────────────┐
│███████████████████│
│████┌───────┐██████│
│████│       │██████│
│████└───────┘██████│
│███████████████████│
└───────────────────┘
```

**Region 2**

```text
┌───────┐
│███████│
└───────┘
```

The hole is not incorrectly converted into a separate region.

---

## Nested Islands

The tool analyzes boundary nesting so complex regions can be separated correctly.

Example:

```text
OUTER REGION
┌─────────────────────────┐
│█████████████████████████│
│███┌───────────────┐█████│
│███│     HOLE      │█████│
│███│   ┌───────┐   │█████│
│███│   │ISLAND │   │█████│
│███│   └───────┘   │█████│
│███└───────────────┘█████│
│█████████████████████████│
└─────────────────────────┘
```

The boundary hierarchy is analyzed as:

```text
Outer Boundary
    └── Hole
          └── Island
                └── Hole
                      └── Island
```

Even-depth boundaries represent filled areas.

Odd-depth boundaries represent holes.

---

# Region Type Preservation

## Filled Regions

Exploded Filled Regions retain the source **Filled Region Type**.

This preserves properties such as:

- Foreground pattern
- Background pattern
- Pattern colors
- Masking behavior defined by the type
- Other Filled Region Type settings

## Masking Regions

Masking Regions remain Masking Regions when exploded.

---

# Boundary Style Preservation

Explode Regions attempts to preserve individual boundary line styles.

For example:

```text
Original Region

Solid ───────────────
                     │
                     │ Hidden
                     │
Dash  ───────────────
```

The resulting region attempts to retain the corresponding source style on each reconstructed boundary segment.

Boundary matching is performed against the original region geometry rather than assigning one line style to the entire resulting region.

---

# Original Region Options

Explode Regions provides:

- **Keep Originals**
- **Delete Originals**

### Keep Originals

The original region remains and the exploded regions are added to the view.

Useful for checking the result.

### Delete Originals

The original region is removed after the exploded regions are successfully created.

---

# Supported Views

The tools operate using the owning view's drawing plane and are intended to work with detail regions in views such as:

- Floor Plans
- Reflected Ceiling Plans
- Sections
- Elevations
- Drafting Views
- Detail Views

Geometry is processed relative to the region's view plane rather than assuming the region lies on the project's global XY plane.

This is important for Sections and Elevations.

---

# Revit Version Considerations

The tools are designed for pyRevit/Revit workflows and IronPython-compatible Revit API access.

### Filled Regions

Filled Region output is supported across the targeted Revit versions.

### Masking Regions

Native creation of new Masking Region output is supported in:

```text
Revit 2024
Revit 2025
Revit 2026
Revit 2027
```

For:

```text
Revit 2022
Revit 2023
```

new Masking Region output is blocked because the required native API functionality is not available in the same form.

---

# Typical Workflows

## Merge Overlapping Regions

1. Select the Filled Regions and/or Masking Regions.
2. Run **Merge Regions**.
3. Choose the output type.
4. Choose the Filled Region Type when applicable.
5. Choose whether to preserve or replace boundary line styles.
6. Choose **Keep Originals** or **Delete Originals**.
7. Click **Merge**.

The resulting geometry is unioned into the minimum required collection of regions while preserving holes and islands.

---

## Explode a Complex Region

1. Select one or more Filled Regions or Masking Regions.
2. Run **Explode Regions**.
3. Choose **Keep Originals** or **Delete Originals**.
4. Run the operation.

Each disconnected filled component becomes an independent Revit region while its associated holes remain intact.

---

# Merge vs. Explode

| Operation | Merge Regions | Explode Regions |
|---|---:|---:|
| Filled Regions | ✓ | ✓ |
| Masking Regions | ✓ | ✓ |
| Preserve holes | ✓ | ✓ |
| Preserve islands | ✓ | ✓ |
| Nested islands | ✓ | ✓ |
| Union overlaps | ✓ | — |
| Split disconnected areas | — | ✓ |
| Preserve Filled Region Type | Optional | ✓ |
| Preserve line styles | ✓ | ✓ |
| Replace line styles | ✓ | — |
| Keep originals | ✓ | ✓ |
| Delete originals | ✓ | ✓ |

---

# Geometry Processing

These tools do more than copy the original Revit boundary loops.

The geometry is analyzed as **2D planar topology** in the owning view.

Conceptually:

```text
Revit Region Geometry
        │
        ▼
Project to View Plane
        │
        ▼
Analyze / Reconstruct Boundaries
        │
        ▼
Determine Loop Containment
        │
        ▼
Classify Fill / Hole / Island
        │
        ▼
Create Valid Revit CurveLoops
        │
        ▼
Create New Region(s)
```

For Merge Regions, overlapping filled geometry is additionally unioned before the final boundary hierarchy is reconstructed.

This is what allows:

```text
┌────────┐
│        │
│    ┌───┼────┐
└────┼───┘    │
     │        │
     └────────┘
```

to become:

```text
┌────────┐
│        │
│        └────┐
│             │
└────┐        │
     │        │
     └────────┘
```

instead of retaining the internal overlap boundaries.

---

# Limitations

Extremely small, degenerate, self-intersecting, or invalid source boundaries may not produce valid Revit regions.

Revit ultimately requires each reconstructed region boundary to consist of valid, closed, planar CurveLoops acceptable to `FilledRegion.Create()` or the corresponding masking-region API.

Geometry that falls below Revit's curve tolerances may therefore be skipped or reported.

Boundary line-style matching may also be approximate where a merge operation creates an entirely new edge that did not exist on any individual source region.

---

# Requirements

- Autodesk Revit
- pyRevit
- Revit API
- IronPython 2.7 compatible environment

The tools are intended for the **PyPiPie** pyRevit extension.

---

# Suggested pyRevit Structure

```text
PyPiPie.extension
└── PyPiPie.tab
    └── Annotation.panel
        └── Filled Regions.pulldown
            │
            ├── Merge Regions.pushbutton
            │   ├── Merge Regions_script.py
            │   ├── icon.png
            │   └── help.html
            │
            └── Explode Regions.pushbutton
                ├── Explode Regions_script.py
                ├── icon.png
                └── help.html
```

---

# Why These Tools Exist

Complex Filled Regions are common in:

- Imported or traced details
- Diagrammatic plans
- Existing-condition graphics
- Phasing graphics
- Code diagrams
- Department or room diagrams
- Presentation graphics
- CAD-to-Revit cleanup workflows

Revit provides limited native tools for restructuring these regions.

**Merge Regions** and **Explode Regions** provide complementary operations:

```text
MULTIPLE REGIONS
       │
       │ Merge
       ▼
COMBINED GEOMETRY
       │
       │ Explode
       ▼
INDIVIDUAL COMPONENTS
```

while attempting to preserve the original graphical appearance and topology.

---

## Author

**Aaron Rumple, AIA**

Part of the **PyPiPie** pyRevit toolset.
