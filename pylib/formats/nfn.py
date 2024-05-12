import json
import re
from collections import defaultdict
from dataclasses import dataclass
from dataclasses import field

import pandas as pd
from dateutil.parser import parse

from pylib import utils
from pylib.fields.box_field import BoxField
from pylib.fields.length_field import LengthField
from pylib.fields.noop_field import NoOpField
from pylib.fields.point_field import PointField
from pylib.fields.same_field import SameField
from pylib.fields.select_field import SelectField
from pylib.fields.text_field import TextField
from pylib.row import Row
from pylib.table import Table

import sys

SUBJECT_PREFIX = 'subject_'

@dataclass
class WorkflowStrings:
    label_strings: dict[str, list[str]] = field(
        default_factory=lambda: defaultdict(list)
    )
    value_strings: dict[str, dict[str, str]] = field(default_factory=dict)


# #####################################################################################
def read(args):
    """Read and convert the input CSV data."""
    df = pd.read_csv(args.input_file, dtype=str)

    args.workflow_id = get_workflow_id(args, df)
    args.workflow_name = get_workflow_name(df)

    df = df.loc[df.workflow_id == str(args.workflow_id), :].fillna("")
    raw_records = df.to_dict("records")

    # A hack to workaround coded values returned from Zooniverse
    workflow_strings = get_workflow_strings(args.workflow_csv, args.workflow_id)

    table = Table()
    for raw_row in raw_records:
        row = Row()

        row.add_field(
            args.group_by,
            SameField(value=raw_row["subject_ids"].split(",", 1)[0]),
        )

        row.add_field(args.row_key, NoOpField(value=raw_row[args.row_key]))

        if args.user_column:
            row.add_field(
                args.user_column, NoOpField(value=raw_row.get(args.user_column, ""))
            )

        # Extract the various json blobs
        extract_annotations(raw_row, row, workflow_strings)
        extract_subject_data(raw_row, row)
        extract_metadata(raw_row, row)
        extract_misc_data(raw_row, row)

        table.append(row)

    return table


# ###################################################################################
def extract_annotations(raw_row: dict, row: Row, workflow_strings: WorkflowStrings):
    """Extract annotations from the json object in the annotations column.

    Annotations are nested json blobs with a WTF format. Part of the WTF is that each
    record can have a different format. It all starts with a list of annotations.
    """
    for anno in json.loads(raw_row["annotations"]):
        flatten_annotation(anno, row, workflow_strings)


def flatten_annotation(anno, row, workflow_strings, anno_id=""):
    """Flatten one annotation recursively."""
    anno_id = anno.get("task", anno_id)

    match anno:
        case {"value": [str(), *__], **___}:
            list_annotation(anno, row, anno_id)
        case {"value": list(), **__}:
            subtask_annotation(anno, row, workflow_strings, anno_id)
        case {"select_label": _, **__}:
            select_label_annotation(anno, row, anno_id)
        case {"task_label": _, **__}:
            task_label_annotation(anno, row, anno_id)
        case {"tool_label": _, "width": __, **___}:
            box_annotation(anno, row, anno_id)
        case {"tool_label": _, "x1": __, **___}:
            length_annotation(anno, row, anno_id)
        case {"tool_label": _, "x": __, **___}:
            point_annotation(anno, row, anno_id)
        case {"tool_label": _, "details": __, **___}:
            workflow_annotation(anno, row, workflow_strings, anno_id)
        case _:
            print(f"Annotation type not found: {anno}")


def subtask_annotation(anno, row, workflow_strings, anno_id):
    """Handle an annotation with subtasks."""
    anno_id = anno.get("task", anno_id)
    for subtask in anno["value"]:
        flatten_annotation(subtask, row, workflow_strings, anno_id)


def list_annotation(anno, row, anno_id):
    values = sorted(anno.get("value", ""))
    key = get_key(anno["task_label"], anno_id)
    row.add_field(key, TextField(value=" ".join(values)))


def select_label_annotation(anno, row, anno_id):
    key = get_key(anno["select_label"], anno_id)
    option = anno.get("option")
    value = anno.get("label", "") if option else anno.get("value", "")
    row.add_field(key, SelectField(value=value))


def task_label_annotation(anno, row, anno_id):
    key = get_key(anno["task_label"], anno_id)
    row.add_field(key, TextField(value=anno.get("value", "")))


def box_annotation(anno, row, anno_id):
    row.add_field(
        get_key(anno["tool_label"], anno_id),
        BoxField(
            left=round(anno["x"]),
            right=round(anno["x"] + anno["width"]),
            top=round(anno["y"]),
            bottom=round(anno["y"] + anno["height"]),
        ),
    )


def length_annotation(anno, row, anno_id):
    row.add_field(
        get_key(anno["tool_label"], anno_id),
        LengthField(
            x1=round(anno["x1"]),
            y1=round(anno["y1"]),
            x2=round(anno["x2"]),
            y2=round(anno["y2"]),
        ),
    )


def point_annotation(anno, row, anno_id):
    row.add_field(
        get_key(anno["tool_label"], anno_id),
        PointField(
            x=round(anno["x"]),
            y=round(anno["y"]),
        ),
    )


def workflow_annotation(anno, row, workflow_strings, anno_id):
    """Get the value of an annotation from workflow data.

    We are trying to match a coded value (UUID-like) with strings in the workflow
    description.The field may be a text value or a (multi-)select value.
    """
    label = "unknown"

    # Get all possible strings for the annotation
    values = []
    for key_, value in workflow_strings.label_strings.items():
        if key_.startswith(anno_id) and key_.endswith("details"):
            values.append(value)
    labels = values[-1] if values else []

    # Loop thru the UUID values
    for i, detail in enumerate(anno["details"]):
        outer_value = detail["value"]

        # If it's a list then we have a (multi-)select value
        if isinstance(outer_value, list):
            values = []

            for item in outer_value:
                # We found the workflow string for the UUID
                if item["value"] in workflow_strings.value_strings:
                    value, label = workflow_strings.value_strings[item["value"]]
                    values.append(value)

                # Paranoia: Cannot find string in workflow
                else:
                    value = item["value"]
                    label = item.get("label", "unknown")
                    values.append(value)

            value = ",".join(v for v in values if v)
            label = f"{anno['tool_label']}.{label}".strip()
            label = get_key(label, anno_id)
            row.add_field(label, TextField(value=value))

        # It's a single text value
        else:
            label = labels[i] if i < len(labels) else "unknown"
            label = f"{anno['tool_label']}.{label}".strip()
            label = get_key(label, anno_id)
            row.add_field(label, TextField(value=outer_value))


def get_key(label: str, anno_id: str):
    return f"~{anno_id}~ {label.strip()}"


# #############################################################################
def extract_subject_data(raw_row, row):
    """Extract subject data from the json object in the subject_data column.

    We prefix the new column names with "subject_" to keep them separate from
    the other data df columns. The subject data json looks like:
        {<subject_id>: {"key_1": "value_1", "key_2": "value_2", ...}}
    """
    annos = json.loads(raw_row["subject_data"])

    for val1 in annos.values():
        for key2, val2 in val1.items():
            if key2 != "retired":
                key2 = re.sub(r'\W+', '_', key2) # Replace non-word chars with _
                key2 = re.sub(r'^_+|_$', '', key2) # Remove leading/trailing _
                row.add_field(SUBJECT_PREFIX + key2, SameField(value=val2))


# #############################################################################
def extract_metadata(raw_row, row):
    """Extract a few field from the metadata JSON object."""
    annos = json.loads(raw_row["metadata"])

    def _date(value):
        return parse(value).strftime("%d-%b-%Y %H:%M:%S")

    row.add_field("started_at", NoOpField(value=_date(annos.get("started_at"))))
    row.add_field("finished_at", NoOpField(value=_date(annos.get("finished_at"))))


# #############################################################################
def extract_misc_data(raw_row, row):
    wanted = """ gold_standard expert workflow_version """.split()
    for key in wanted:
        if (value := raw_row.get(key)) is not None:
            row.add_field(key, NoOpField(value=value))


# #############################################################################
def get_workflow_id(args, df):
    """Pull the workflow ID from the data frame if it was not given."""
    if args.workflow_id:
        return args.workflow_id

    workflow_ids = df.workflow_id.unique()

    if len(workflow_ids) > 1:
        utils.error_exit(
            "There are multiple workflows in this file. "
            "You must provide a workflow ID as an argument."
        )

    return workflow_ids[0]


def get_workflow_name(df):
    """Extract and format the workflow name from the data df."""
    workflow_name = ""
    try:
        workflow_name = df.workflow_name.iloc[0]
        workflow_name = re.sub(r"^[^_]*_", "", workflow_name)
    except KeyError:
        utils.error_exit("Workflow name not found in classifications file.")
    return workflow_name


def get_workflow_strings(workflow_csv, workflow_id):
    """Get strings from the workflow for when they're not in the annotations."""
    value_strings = {}
    if not workflow_csv:
        return value_strings

    # Read the workflow
    df = pd.read_csv(workflow_csv)
    df = df.loc[df.workflow_id == int(workflow_id), :]
    workflow = df.iloc[-1]  # Get the most recent version

    strings = json.loads(workflow["strings"]).items()

    instructions = {}
    label_strings = defaultdict(list)
    for key, value in strings.items():
        if key.endswith("instruction"):
            parts = key.split(".")
            key = ".".join(parts[:-1])
            value = value.strip()
            instructions[key] = value
            key = ".".join(parts[:-2])
            if key:
                label_strings[key].append(value)

    # Recursively go thru the workflow strings
    def _task_dive(node):
        match node:
            case {"value": val, "label": label, **___}:
                if string := strings.get(label):
                    string = string.strip()
                    label = label.strip()
                    labels = [v for k, v in instructions.items() if label.startswith(k)]
                    label = labels[-1] if labels else ""
                    value_strings[val] = {string: label}
            case dict():
                for child in node.values():
                    _task_dive(child)
            case list():
                for child in node:
                    _task_dive(child)

    annos = json.loads(workflow["tasks"])
    _task_dive(annos)

    return WorkflowStrings(label_strings=label_strings, value_strings=value_strings)
