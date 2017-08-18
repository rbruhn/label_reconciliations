"""Merge reconciled, explanations, unreconciled dataframes into one."""

import pandas as pd
import lib.util as util

ROW_TYPES = {  # Row types and their sort order
    'reconciled': '1-reconciled',
    'explanations': '2-explanations',
    'unreconciled': '3-unreconciled'}


def merge(
        args, unreconciled, reconciled, explanations, column_types):
    """Combine dataframes.

    Make sure they are grouped by subject ID. Also sort them within each
    subject ID group.
    """
    # Make the index a column
    rec = reconciled.reset_index()
    exp = explanations.reset_index()
    unr = unreconciled.astype(object).copy()

    # Sort by group-by then by row_type and then key-column
    rec['row_type'] = ROW_TYPES['reconciled']
    exp['row_type'] = ROW_TYPES['explanations']
    unr['row_type'] = ROW_TYPES['unreconciled']

    # Merge and format the dataframes
    merged = pd.concat([rec, exp, unr])
    columns = util.sort_columns(args, merged.columns, column_types)
    merged = merged.reindex_axis(columns, axis=1).fillna('')
    merged.sort_values(
        [args.group_by, 'row_type', args.key_column], inplace=True)

    return merged