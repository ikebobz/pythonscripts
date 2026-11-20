import re
import sys
from functools import reduce
from pathlib import Path

import pandas as pd

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableView,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


# ---------------------------------------------------------------------------
# Filtering helpers
# ---------------------------------------------------------------------------

MATCH_ALL = "All conditions (AND)"
MATCH_ANY = "Any condition (OR)"

NUMERIC_COMPARISONS = {
    "greater than": lambda numbers, threshold: numbers > threshold,
    "greater than or equal to": lambda numbers, threshold: numbers >= threshold,
    "less than": lambda numbers, threshold: numbers < threshold,
    "less than or equal to": lambda numbers, threshold: numbers <= threshold,
}

FILTER_OPERATORS = [
    "contains",
    "does not contain",
    "equals",
    "does not equal",
    "is one of",
    "is not one of",
    "starts with",
    "ends with",
    *NUMERIC_COMPARISONS.keys(),
    "between",
    "is blank",
    "is not blank",
]

VALUE_FREE_OPERATORS = {"is blank", "is not blank"}

VALUE_PLACEHOLDERS = {
    "is one of": "Values separated by commas, e.g. P001, P002",
    "is not one of": "Values separated by commas, e.g. P001, P002",
    "between": "Range, inclusive, e.g. 18 to 65",
    "is blank": "(no value needed)",
    "is not blank": "(no value needed)",
}


def to_text(series):
    """Return a series as trimmed text with blanks as empty strings."""
    return series.astype("string").fillna("").str.strip()


def to_number(series):
    """Convert text to float; blanks and non-numeric text become NaN.

    Thousands separators (commas) are removed before conversion.
    """
    cleaned = to_text(series).str.replace(",", "", regex=False)
    return pd.to_numeric(cleaned.astype(object), errors="coerce").astype("float64")


def parse_number(value, column):
    try:
        return float(str(value).strip().replace(",", ""))
    except ValueError:
        raise ValueError(
            f"Filter on '{column}': '{value}' is not a number."
        ) from None


def parse_value_list(value):
    return {
        part.strip().lower()
        for part in re.split(r"[,;\n]", value)
        if part.strip()
    }


def parse_range(value, column):
    parts = [
        part
        for part in re.split(
            r"\s*(?:\bto\b|\band\b|;|\.\.)\s*", value.strip(), flags=re.IGNORECASE
        )
        if part
    ]
    if len(parts) != 2:
        raise ValueError(
            f"Filter on '{column}': enter a range such as '18 to 65'."
        )
    low, high = (parse_number(part, column) for part in parts)
    return min(low, high), max(low, high)


def build_filter_mask(dataframe, column, operator, value):
    """Return a boolean Series selecting rows that satisfy one condition.

    Text comparisons are case-insensitive and ignore surrounding spaces.
    Numeric comparisons skip cells that are blank or not numbers.
    """
    if column not in dataframe.columns:
        raise ValueError(f"Column '{column}' is not in the results.")

    text = to_text(dataframe[column])
    lowered = text.str.lower()
    target = value.strip().lower()

    if operator == "is blank":
        mask = text == ""
    elif operator == "is not blank":
        mask = text != ""
    elif operator == "contains":
        mask = lowered.str.contains(target, regex=False)
    elif operator == "does not contain":
        mask = ~lowered.str.contains(target, regex=False)
    elif operator == "equals":
        mask = lowered == target
    elif operator == "does not equal":
        mask = lowered != target
    elif operator in ("is one of", "is not one of"):
        options = parse_value_list(value)
        if not options:
            raise ValueError(f"Filter on '{column}': enter at least one value.")
        mask = lowered.isin(options)
        if operator == "is not one of":
            mask = ~mask
    elif operator == "starts with":
        mask = lowered.str.startswith(target)
    elif operator == "ends with":
        mask = lowered.str.endswith(target)
    elif operator in NUMERIC_COMPARISONS:
        threshold = parse_number(value, column)
        mask = NUMERIC_COMPARISONS[operator](to_number(dataframe[column]), threshold)
    elif operator == "between":
        low, high = parse_range(value, column)
        mask = to_number(dataframe[column]).between(low, high)
    else:
        raise ValueError(f"Unknown filter operator '{operator}'.")

    return pd.Series(mask, index=dataframe.index).fillna(False).astype(bool)


def sort_key(series):
    """Sort numerically when every nonblank value is a number, else as text."""
    text = to_text(series)
    numbers = to_number(series)
    nonblank = (text != "").to_numpy()
    if nonblank.any() and numbers[nonblank].notna().all():
        return numbers
    return text.str.lower()


# ---------------------------------------------------------------------------
# Qt model and widgets
# ---------------------------------------------------------------------------


class DataFrameModel(QAbstractTableModel):
    """Read-only, sortable Qt model for rendering a pandas DataFrame."""

    def __init__(self, dataframe=None):
        super().__init__()
        self._dataframe = dataframe if dataframe is not None else pd.DataFrame()
        self._sort_column_name = None
        self._sort_ascending = True

    def set_dataframe(self, dataframe):
        self.beginResetModel()
        self._dataframe = self._sorted(dataframe.copy())
        self.endResetModel()

    def dataframe(self):
        """The rows currently displayed, in their displayed order."""
        return self._dataframe.copy()

    def _sorted(self, dataframe):
        name = self._sort_column_name
        if name is None or name not in dataframe.columns or dataframe.empty:
            return dataframe
        return dataframe.sort_values(
            by=name,
            key=sort_key,
            ascending=self._sort_ascending,
            na_position="last",
            kind="stable",
        )

    def sort(self, column, order=Qt.SortOrder.AscendingOrder):
        if self._dataframe.empty or not 0 <= column < len(self._dataframe.columns):
            return

        self._sort_column_name = self._dataframe.columns[column]
        self._sort_ascending = order == Qt.SortOrder.AscendingOrder

        self.layoutAboutToBeChanged.emit()
        self._dataframe = self._sorted(self._dataframe)
        self.layoutChanged.emit()

    def rowCount(self, parent=QModelIndex()):
        if parent.isValid():
            return 0
        return len(self._dataframe.index)

    def columnCount(self, parent=QModelIndex()):
        if parent.isValid():
            return 0
        return len(self._dataframe.columns)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None

        if role == Qt.ItemDataRole.DisplayRole:
            value = self._dataframe.iloc[index.row(), index.column()]

            if pd.isna(value):
                return ""

            if isinstance(value, float):
                return f"{value:,.2f}"

            return str(value)

        if role == Qt.ItemDataRole.TextAlignmentRole:
            value = self._dataframe.iloc[index.row(), index.column()]
            if isinstance(value, (int, float)) and not pd.isna(value):
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None

        if orientation == Qt.Orientation.Horizontal:
            return str(self._dataframe.columns[section])

        return str(section + 1)


class FilterRow(QWidget):
    """One filter condition: column, operator, value, and a remove button."""

    def __init__(self, columns, remove_callback, apply_callback):
        super().__init__()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.column_combo = QComboBox()
        self.column_combo.addItems(columns)
        self.column_combo.setMinimumWidth(150)
        layout.addWidget(self.column_combo, 2)

        self.operator_combo = QComboBox()
        self.operator_combo.addItems(FILTER_OPERATORS)
        self.operator_combo.currentTextChanged.connect(self.update_value_field)
        layout.addWidget(self.operator_combo, 2)

        self.value_edit = QLineEdit()
        self.value_edit.returnPressed.connect(apply_callback)
        layout.addWidget(self.value_edit, 3)

        remove_button = QPushButton("Remove")
        remove_button.clicked.connect(lambda: remove_callback(self))
        layout.addWidget(remove_button)

        self.update_value_field(self.operator_combo.currentText())

    def update_value_field(self, operator):
        self.value_edit.setEnabled(operator not in VALUE_FREE_OPERATORS)
        self.value_edit.setPlaceholderText(VALUE_PLACEHOLDERS.get(operator, "Value"))

    def set_columns(self, columns):
        current = self.column_combo.currentText()
        self.column_combo.clear()
        self.column_combo.addItems(columns)
        if current in columns:
            self.column_combo.setCurrentText(current)

    def is_complete(self):
        operator = self.operator_combo.currentText()
        return bool(self.column_combo.currentText()) and (
            operator in VALUE_FREE_OPERATORS or bool(self.value_edit.text().strip())
        )

    def condition(self):
        return (
            self.column_combo.currentText(),
            self.operator_combo.currentText(),
            self.value_edit.text(),
        )

    def describe(self):
        column, operator, value = self.condition()
        if operator in VALUE_FREE_OPERATORS:
            return f"{column} {operator}"
        return f'{column} {operator} "{value.strip()}"'


class PatientCrossReferenceApp(QMainWindow):
    def __init__(self):
        super().__init__()

        self.id_file_path = None
        self.reference_file_path = None

        self.id_excel = None
        self.reference_excel = None

        self.id_dataframe = None
        self.reference_dataframe = None
        self.results_dataframe = None
        self.filtered_dataframe = None

        self.filter_rows = []
        self.active_filter_descriptions = []
        self.base_summary_lines = []

        self.setWindowTitle("Patient ID Cross-Reference Tool")
        self.resize(1400, 900)

        self.build_ui()

    def build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)

        main_layout = QVBoxLayout(root)

        title = QLabel("Patient ID Cross-Reference Tool")
        title.setStyleSheet("font-size: 20px; font-weight: bold;")
        main_layout.addWidget(title)

        description = QLabel(
            "Load a patient-ID workbook and a reference workbook, select the matching "
            "identifier columns and desired fields, then run a local cross-reference."
        )
        description.setWordWrap(True)
        main_layout.addWidget(description)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter, 1)

        controls = QWidget()
        controls_layout = QVBoxLayout(controls)
        splitter.addWidget(controls)

        results_panel = QWidget()
        results_layout = QVBoxLayout(results_panel)
        splitter.addWidget(results_panel)

        splitter.setSizes([390, 1010])

        id_group = QGroupBox("1. Patient ID source")
        id_layout = QFormLayout(id_group)

        self.load_id_button = QPushButton("Load patient-ID Excel file")
        self.load_id_button.clicked.connect(self.load_id_file)
        id_layout.addRow(self.load_id_button)

        self.id_file_label = QLabel("No file loaded")
        self.id_file_label.setWordWrap(True)
        id_layout.addRow("File:", self.id_file_label)

        self.id_sheet_combo = QComboBox()
        self.id_sheet_combo.currentTextChanged.connect(self.load_id_sheet)
        id_layout.addRow("Sheet:", self.id_sheet_combo)

        self.id_column_combo = QComboBox()
        id_layout.addRow("Patient ID column:", self.id_column_combo)

        controls_layout.addWidget(id_group)

        reference_group = QGroupBox("2. Reference data")
        reference_layout = QFormLayout(reference_group)

        self.load_reference_button = QPushButton("Load reference Excel file")
        self.load_reference_button.clicked.connect(self.load_reference_file)
        reference_layout.addRow(self.load_reference_button)

        self.reference_file_label = QLabel("No file loaded")
        self.reference_file_label.setWordWrap(True)
        reference_layout.addRow("File:", self.reference_file_label)

        self.reference_sheet_combo = QComboBox()
        self.reference_sheet_combo.currentTextChanged.connect(self.load_reference_sheet)
        reference_layout.addRow("Sheet:", self.reference_sheet_combo)

        self.reference_id_column_combo = QComboBox()
        reference_layout.addRow("Reference ID column:", self.reference_id_column_combo)

        controls_layout.addWidget(reference_group)

        columns_group = QGroupBox("3. Columns to retrieve")
        columns_layout = QVBoxLayout(columns_group)

        column_help = QLabel(
            "Select one or more fields from the reference sheet. "
            "Use Ctrl or Shift to select multiple fields."
        )
        column_help.setWordWrap(True)
        columns_layout.addWidget(column_help)

        self.columns_list = QListWidget()
        self.columns_list.setSelectionMode(
            QAbstractItemView.SelectionMode.MultiSelection
        )
        columns_layout.addWidget(self.columns_list)

        select_buttons = QHBoxLayout()

        self.select_all_button = QPushButton("Select all")
        self.select_all_button.clicked.connect(self.select_all_columns)
        select_buttons.addWidget(self.select_all_button)

        self.clear_selection_button = QPushButton("Clear selection")
        self.clear_selection_button.clicked.connect(self.clear_column_selection)
        select_buttons.addWidget(self.clear_selection_button)

        columns_layout.addLayout(select_buttons)

        controls_layout.addWidget(columns_group, 1)

        action_group = QGroupBox("4. Process")
        action_layout = QVBoxLayout(action_group)

        self.run_button = QPushButton("Run cross-reference")
        self.run_button.setStyleSheet("font-weight: bold; padding: 8px;")
        self.run_button.clicked.connect(self.run_cross_reference)
        action_layout.addWidget(self.run_button)

        self.export_button = QPushButton("Export results to Excel")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self.export_results)
        action_layout.addWidget(self.export_button)

        controls_layout.addWidget(action_group)
        controls_layout.addStretch()

        summary_group = QGroupBox("Summary analysis")
        summary_layout = QVBoxLayout(summary_group)

        self.summary_text = QTextEdit()
        self.summary_text.setReadOnly(True)
        self.summary_text.setMinimumHeight(160)
        self.summary_text.setPlaceholderText(
            "Summary statistics will appear after the cross-reference runs."
        )
        summary_layout.addWidget(self.summary_text)

        results_layout.addWidget(summary_group)

        results_layout.addWidget(self.build_filter_group())

        table_label = QLabel("Cross-reference results")
        table_label.setStyleSheet("font-weight: bold;")
        results_layout.addWidget(table_label)

        self.table_model = DataFrameModel()
        self.results_table = QTableView()
        self.results_table.setModel(self.table_model)
        self.results_table.setAlternatingRowColors(True)
        self.results_table.horizontalHeader().setSortIndicator(
            -1, Qt.SortOrder.AscendingOrder
        )
        self.results_table.setSortingEnabled(True)
        self.results_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.results_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.results_table.horizontalHeader().setStretchLastSection(True)

        results_layout.addWidget(self.results_table, 1)

        self.status_label = QLabel("Ready. Load the patient-ID source file first.")
        self.status_label.setFrameShape(QFrame.Shape.StyledPanel)
        main_layout.addWidget(self.status_label)

    def build_filter_group(self):
        self.filter_group = QGroupBox("Filter results")
        filter_layout = QVBoxLayout(self.filter_group)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Show rows matching:"))

        self.filter_mode_combo = QComboBox()
        self.filter_mode_combo.addItems([MATCH_ALL, MATCH_ANY])
        self.filter_mode_combo.currentTextChanged.connect(
            lambda _text: self.apply_filters()
        )
        controls.addWidget(self.filter_mode_combo)
        controls.addStretch()

        add_button = QPushButton("Add filter")
        add_button.clicked.connect(self.add_filter_row)
        controls.addWidget(add_button)

        apply_button = QPushButton("Apply filters")
        apply_button.setStyleSheet("font-weight: bold;")
        apply_button.clicked.connect(self.apply_filters)
        controls.addWidget(apply_button)

        clear_button = QPushButton("Clear filters")
        clear_button.clicked.connect(self.clear_filters)
        controls.addWidget(clear_button)

        filter_layout.addLayout(controls)

        rows_container = QWidget()
        self.filter_rows_layout = QVBoxLayout(rows_container)
        self.filter_rows_layout.setContentsMargins(0, 0, 0, 0)
        self.filter_rows_layout.addStretch()

        self.filter_scroll = QScrollArea()
        self.filter_scroll.setWidgetResizable(True)
        self.filter_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.filter_scroll.setWidget(rows_container)
        self.filter_scroll.setMaximumHeight(170)
        self.filter_scroll.setVisible(False)
        filter_layout.addWidget(self.filter_scroll)

        self.filter_status_label = QLabel(
            "Run a cross-reference, then add filters. Press Enter in a value box "
            "or click Apply filters to update the table."
        )
        self.filter_status_label.setWordWrap(True)
        filter_layout.addWidget(self.filter_status_label)

        self.filter_group.setEnabled(False)
        return self.filter_group

    def show_error(self, title, message):
        QMessageBox.critical(self, title, message)

    def show_warning(self, title, message):
        QMessageBox.warning(self, title, message)

    def choose_excel_file(self, caption):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            caption,
            "",
            "Excel files (*.xlsx *.xlsm *.xls);;All files (*.*)",
        )
        return file_path

    def safe_read_excel(self, excel_file, sheet_name):
        """
        Read all fields as text so IDs retain leading zeroes and do not become
        floating-point values. Empty cells remain missing values.
        """
        return pd.read_excel(
            excel_file,
            sheet_name=sheet_name,
            dtype=str,
            keep_default_na=False,
        )

    def clean_column_names(self, dataframe):
        dataframe = dataframe.copy()
        dataframe.columns = [
            str(column).strip() if str(column).strip() else f"Unnamed_{index}"
            for index, column in enumerate(dataframe.columns)
        ]
        return dataframe

    def normalize_patient_id(self, series):
        """
        Match IDs robustly without changing their displayed value.

        Converts IDs to strings, removes surrounding whitespace, and treats
        empty IDs as missing. It does not remove internal spaces or punctuation.
        """
        normalized = series.astype("string").str.strip()
        normalized = normalized.replace("", pd.NA)
        return normalized

    def load_id_file(self):
        file_path = self.choose_excel_file("Choose patient-ID Excel file")
        if not file_path:
            return

        try:
            self.id_excel = pd.ExcelFile(file_path)
            self.id_file_path = file_path

            self.id_file_label.setText(Path(file_path).name)

            self.id_sheet_combo.blockSignals(True)
            self.id_sheet_combo.clear()
            self.id_sheet_combo.addItems(self.id_excel.sheet_names)
            self.id_sheet_combo.blockSignals(False)

            if self.id_excel.sheet_names:
                self.id_sheet_combo.setCurrentIndex(0)
                self.load_id_sheet(self.id_excel.sheet_names[0])

            self.status_label.setText(
                f"Loaded patient-ID workbook: {Path(file_path).name}"
            )

        except Exception as error:
            self.show_error(
                "Unable to load patient-ID workbook",
                f"Could not open the selected file.\n\n{error}",
            )

    def load_id_sheet(self, sheet_name):
        if self.id_excel is None or not sheet_name:
            return

        try:
            dataframe = self.safe_read_excel(self.id_excel, sheet_name)
            self.id_dataframe = self.clean_column_names(dataframe)

            self.id_column_combo.clear()
            self.id_column_combo.addItems(self.id_dataframe.columns.tolist())

            self.status_label.setText(
                f"Loaded patient-ID sheet '{sheet_name}' with "
                f"{len(self.id_dataframe):,} rows."
            )

        except Exception as error:
            self.show_error(
                "Unable to read patient-ID sheet",
                f"Could not read sheet '{sheet_name}'.\n\n{error}",
            )

    def load_reference_file(self):
        file_path = self.choose_excel_file("Choose reference Excel file")
        if not file_path:
            return

        try:
            self.reference_excel = pd.ExcelFile(file_path)
            self.reference_file_path = file_path

            self.reference_file_label.setText(Path(file_path).name)

            self.reference_sheet_combo.blockSignals(True)
            self.reference_sheet_combo.clear()
            self.reference_sheet_combo.addItems(self.reference_excel.sheet_names)
            self.reference_sheet_combo.blockSignals(False)

            if self.reference_excel.sheet_names:
                self.reference_sheet_combo.setCurrentIndex(0)
                self.load_reference_sheet(self.reference_excel.sheet_names[0])

            self.status_label.setText(
                f"Loaded reference workbook: {Path(file_path).name}"
            )

        except Exception as error:
            self.show_error(
                "Unable to load reference workbook",
                f"Could not open the selected file.\n\n{error}",
            )

    def load_reference_sheet(self, sheet_name):
        if self.reference_excel is None or not sheet_name:
            return

        try:
            dataframe = self.safe_read_excel(self.reference_excel, sheet_name)
            self.reference_dataframe = self.clean_column_names(dataframe)

            columns = self.reference_dataframe.columns.tolist()

            self.reference_id_column_combo.clear()
            self.reference_id_column_combo.addItems(columns)

            self.columns_list.clear()
            for column in columns:
                item = QListWidgetItem(column)
                self.columns_list.addItem(item)

            self.status_label.setText(
                f"Loaded reference sheet '{sheet_name}' with "
                f"{len(self.reference_dataframe):,} rows and "
                f"{len(columns):,} columns."
            )

        except Exception as error:
            self.show_error(
                "Unable to read reference sheet",
                f"Could not read sheet '{sheet_name}'.\n\n{error}",
            )

    def select_all_columns(self):
        for index in range(self.columns_list.count()):
            self.columns_list.item(index).setSelected(True)

    def clear_column_selection(self):
        self.columns_list.clearSelection()

    def selected_reference_columns(self):
        return [
            item.text()
            for item in self.columns_list.selectedItems()
        ]

    def run_cross_reference(self):
        if self.id_dataframe is None:
            self.show_warning(
                "Patient-ID file required",
                "Load a patient-ID workbook and select its patient-ID column first.",
            )
            return

        if self.reference_dataframe is None:
            self.show_warning(
                "Reference file required",
                "Load a reference workbook and select its patient-ID column first.",
            )
            return

        id_column = self.id_column_combo.currentText()
        reference_id_column = self.reference_id_column_combo.currentText()
        retrieve_columns = self.selected_reference_columns()

        if not id_column:
            self.show_warning(
                "Patient ID column required",
                "Select the patient-ID column in the source workbook.",
            )
            return

        if not reference_id_column:
            self.show_warning(
                "Reference ID column required",
                "Select the matching patient-ID column in the reference workbook.",
            )
            return

        if not retrieve_columns:
            self.show_warning(
                "Reference columns required",
                "Select at least one reference column to retrieve.",
            )
            return

        try:
            source = self.id_dataframe[[id_column]].copy()
            source = source.rename(columns={id_column: "Patient ID"})
            source["Patient ID"] = self.normalize_patient_id(source["Patient ID"])
            source["_Source order"] = range(len(source))

            source_nonblank = source[source["Patient ID"].notna()].copy()
            blank_id_count = len(source) - len(source_nonblank)

            reference_columns = list(
                dict.fromkeys([reference_id_column] + retrieve_columns)
            )

            reference = self.reference_dataframe[reference_columns].copy()
            reference = reference.rename(
                columns={reference_id_column: "_Reference patient ID"}
            )
            reference["_Reference patient ID"] = self.normalize_patient_id(
                reference["_Reference patient ID"]
            )

            reference_nonblank = reference[
                reference["_Reference patient ID"].notna()
            ].copy()

            duplicate_reference_ids = int(
                reference_nonblank["_Reference patient ID"].duplicated(keep=False).sum()
            )

            results = source.merge(
                reference_nonblank,
                how="left",
                left_on="Patient ID",
                right_on="_Reference patient ID",
                indicator=True,
                sort=False,
            )

            results["Match status"] = results["_merge"].map(
                {
                    "both": "Matched",
                    "left_only": "Not found",
                    "right_only": "Not found",
                }
            )

            results.loc[results["Patient ID"].isna(), "Match status"] = "Blank source ID"

            results = results.sort_values("_Source order", kind="stable")

            output_columns = ["Patient ID"] + [
                column for column in retrieve_columns
                if column != reference_id_column
            ]

            if reference_id_column in retrieve_columns:
                output_columns.append(reference_id_column)

            output_columns.append("Match status")

            output_columns = [
                column for column in output_columns
                if column in results.columns
            ]

            self.results_dataframe = results[output_columns].reset_index(drop=True)

            requested_nonblank_ids = int(source_nonblank["Patient ID"].nunique())
            total_input_rows = len(source)
            matched_rows = int(
                (self.results_dataframe["Match status"] == "Matched").sum()
            )
            unmatched_rows = int(
                (self.results_dataframe["Match status"] == "Not found").sum()
            )
            duplicate_source_ids = int(
                source_nonblank["Patient ID"].duplicated(keep=False).sum()
            )

            match_rate = (
                (matched_rows / len(source_nonblank) * 100)
                if len(source_nonblank) > 0
                else 0
            )

            self.update_summary(
                total_input_rows=total_input_rows,
                requested_nonblank_ids=requested_nonblank_ids,
                matched_rows=matched_rows,
                unmatched_rows=unmatched_rows,
                blank_id_count=blank_id_count,
                duplicate_source_ids=duplicate_source_ids,
                duplicate_reference_ids=duplicate_reference_ids,
                match_rate=match_rate,
            )

            # Keep existing filters, pointing them at the new result columns.
            result_columns = self.results_dataframe.columns.tolist()
            for row in self.filter_rows:
                row.set_columns(result_columns)
            self.filter_group.setEnabled(True)

            self.apply_filters()
            self.results_table.resizeColumnsToContents()
            self.export_button.setEnabled(True)

            self.status_label.setText(
                f"Cross-reference complete: {matched_rows:,} matched row(s), "
                f"{unmatched_rows:,} unmatched row(s)."
            )

            if duplicate_reference_ids > 0:
                self.show_warning(
                    "Duplicate reference IDs detected",
                    "The reference sheet contains duplicate nonblank patient IDs. "
                    "A source ID can therefore produce more than one result row. "
                    "Review the output before using it for reporting or decisions.",
                )

        except Exception as error:
            self.show_error(
                "Cross-reference failed",
                f"The files could not be matched.\n\n{error}",
            )

    # ----------------------------------------------------------------------
    # Filtering
    # ----------------------------------------------------------------------

    def add_filter_row(self):
        if self.results_dataframe is None:
            return

        row = FilterRow(
            self.results_dataframe.columns.tolist(),
            self.remove_filter_row,
            self.apply_filters,
        )
        self.filter_rows.append(row)
        self.filter_rows_layout.insertWidget(self.filter_rows_layout.count() - 1, row)
        self.filter_scroll.setVisible(True)
        row.value_edit.setFocus()
        self.update_filter_status()

    def remove_filter_row(self, row):
        if row in self.filter_rows:
            self.filter_rows.remove(row)
        self.filter_rows_layout.removeWidget(row)
        row.deleteLater()
        self.filter_scroll.setVisible(bool(self.filter_rows))
        self.apply_filters()

    def clear_filters(self):
        for row in list(self.filter_rows):
            self.filter_rows_layout.removeWidget(row)
            row.deleteLater()
        self.filter_rows.clear()
        self.filter_scroll.setVisible(False)
        self.apply_filters()

    def apply_filters(self):
        if self.results_dataframe is None:
            return

        results = self.results_dataframe
        active_rows = [row for row in self.filter_rows if row.is_complete()]

        if active_rows:
            try:
                masks = [
                    build_filter_mask(results, *row.condition())
                    for row in active_rows
                ]
            except ValueError as error:
                self.show_warning("Invalid filter", str(error))
                return

            if self.filter_mode_combo.currentText() == MATCH_ANY:
                mask = reduce(lambda left, right: left | right, masks)
            else:
                mask = reduce(lambda left, right: left & right, masks)

            self.filtered_dataframe = results[mask].copy()
        else:
            self.filtered_dataframe = results.copy()

        self.active_filter_descriptions = [row.describe() for row in active_rows]

        self.table_model.set_dataframe(self.filtered_dataframe)
        self.update_filter_status()
        self.render_summary()

    def update_filter_status(self):
        if self.results_dataframe is None or self.filtered_dataframe is None:
            return

        total = len(self.results_dataframe)
        shown = len(self.filtered_dataframe)
        active = len(self.active_filter_descriptions)

        text = f"Showing {shown:,} of {total:,} rows"
        if active:
            mode = "all" if self.filter_mode_combo.currentText() == MATCH_ALL else "any"
            text += f" — matching {mode} of {active:,} filter(s)."
        else:
            text += " — no filters applied."

        pending = len(self.filter_rows) - sum(
            1 for row in self.filter_rows if row.is_complete()
        )
        if pending:
            text += f" {pending:,} filter(s) have no value yet and are ignored."

        self.filter_status_label.setText(text)

    # ----------------------------------------------------------------------
    # Summary
    # ----------------------------------------------------------------------

    def numeric_summary_lines(self, dataframe):
        lines = []
        for column in dataframe.columns:
            if column in ("Patient ID", "Match status"):
                continue

            numeric_values = to_number(dataframe[column]).dropna()

            if not numeric_values.empty:
                lines.append(
                    f"{column}: n={len(numeric_values):,}, "
                    f"mean={numeric_values.mean():,.2f}, "
                    f"min={numeric_values.min():,.2f}, "
                    f"max={numeric_values.max():,.2f}"
                )
        return lines

    def update_summary(
        self,
        total_input_rows,
        requested_nonblank_ids,
        matched_rows,
        unmatched_rows,
        blank_id_count,
        duplicate_source_ids,
        duplicate_reference_ids,
        match_rate,
    ):
        summary_lines = [
            "CROSS-REFERENCE SUMMARY",
            "",
            f"Input patient-ID rows: {total_input_rows:,}",
            f"Unique nonblank patient IDs: {requested_nonblank_ids:,}",
            f"Matched output rows: {matched_rows:,}",
            f"Unmatched output rows: {unmatched_rows:,}",
            f"Blank source-ID rows: {blank_id_count:,}",
            f"Source rows belonging to duplicated IDs: {duplicate_source_ids:,}",
            f"Reference rows belonging to duplicated IDs: {duplicate_reference_ids:,}",
            f"Row-level match rate: {match_rate:.1f}%",
        ]

        if self.results_dataframe is not None and not self.results_dataframe.empty:
            numeric_lines = self.numeric_summary_lines(self.results_dataframe)
            if numeric_lines:
                summary_lines.extend(["", "NUMERIC FIELD SUMMARY (ALL RESULTS)"])
                summary_lines.extend(numeric_lines)

        self.base_summary_lines = summary_lines
        self.render_summary()

    def render_summary(self):
        lines = list(self.base_summary_lines)

        if self.active_filter_descriptions and self.filtered_dataframe is not None:
            mode = self.filter_mode_combo.currentText()
            lines.extend(
                [
                    "",
                    "FILTERED VIEW",
                    f"Rows shown: {len(self.filtered_dataframe):,} of "
                    f"{len(self.results_dataframe):,}",
                    f"Combine: {mode}",
                    "Filters:",
                ]
            )
            lines.extend(f"  - {text}" for text in self.active_filter_descriptions)

            if "Match status" in self.filtered_dataframe.columns:
                counts = self.filtered_dataframe["Match status"].value_counts()
                for status, count in counts.items():
                    lines.append(f"{status} rows: {count:,}")

            numeric_lines = self.numeric_summary_lines(self.filtered_dataframe)
            if numeric_lines:
                lines.extend(["", "NUMERIC FIELD SUMMARY (FILTERED ROWS)"])
                lines.extend(numeric_lines)

        self.summary_text.setPlainText("\n".join(lines))

    # ----------------------------------------------------------------------
    # Export
    # ----------------------------------------------------------------------

    def export_results(self):
        if self.results_dataframe is None or self.results_dataframe.empty:
            self.show_warning(
                "No results available",
                "Run a cross-reference before exporting results.",
            )
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export cross-reference results",
            "patient_cross_reference_results.xlsx",
            "Excel files (*.xlsx)",
        )

        if not file_path:
            return

        if not file_path.lower().endswith(".xlsx"):
            file_path += ".xlsx"

        try:
            summary_lines = self.summary_text.toPlainText().splitlines()
            summary_dataframe = pd.DataFrame({"Summary": summary_lines})

            with pd.ExcelWriter(file_path, engine="openpyxl") as writer:
                if self.active_filter_descriptions:
                    # Rows exactly as shown in the table: filtered and sorted.
                    self.table_model.dataframe().to_excel(
                        writer,
                        sheet_name="Filtered results",
                        index=False,
                    )
                self.results_dataframe.to_excel(
                    writer,
                    sheet_name="Cross-reference results",
                    index=False,
                )
                summary_dataframe.to_excel(
                    writer,
                    sheet_name="Summary",
                    index=False,
                )

            self.status_label.setText(
                f"Results exported successfully: {Path(file_path).name}"
            )

            QMessageBox.information(
                self,
                "Export complete",
                f"Results were saved to:\n{file_path}",
            )

        except Exception as error:
            self.show_error(
                "Export failed",
                f"Could not export the results file.\n\n{error}",
            )


def main():
    application = QApplication(sys.argv)
    application.setStyle("Fusion")

    window = PatientCrossReferenceApp()
    window.show()

    sys.exit(application.exec())


if __name__ == "__main__":
    main()
