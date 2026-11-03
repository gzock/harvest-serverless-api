import os
import logging
from io import BytesIO
from datetime import datetime, timedelta, timezone
import openpyxl
from openpyxl.utils import (
    get_column_letter,
    rows_from_range,
    cols_from_range,
)
from openpyxl.styles.fonts import Font
from openpyxl.worksheet.pagebreak import Break

# from openpyxl.utils.cell import coordinate_from_string, column_index_from_string
from PIL import Image, ImageOps
from openpyxl.drawing.spreadsheet_drawing import TwoCellAnchor, AnchorMarker
from openpyxl.utils.units import pixels_to_EMU

from copy import copy
from openpyxl.worksheet.print_settings import PrintArea
from openpyxl.utils.cell import range_boundaries
from openpyxl.formula.translate import Translator


class ExcelDocGenerator:
    logger = logging.getLogger(__name__)
    logger.addHandler(logging.NullHandler())

    def __init__(self, workbook=None, worksheet=None):
        if workbook:
            self.load_workbook(workbook)
            self.active_worksheet(worksheet)

    def load_workbook(self, filename):
        self.logger.debug("loading filename: %s" % filename)
        # Own the descriptor so invalid XML cannot leave an open ZIP on warm Lambdas.
        with open(filename, "rb") as source:
            self.wb = openpyxl.load_workbook(
                filename=source, read_only=False, keep_vba=False, data_only=False, keep_links=False
            )
        self.logger.debug("loading done")

    def active_worksheet(self, sheet_name=0):
        if self.wb:
            if sheet_name:
                self.ws = self.wb[sheet_name]
            else:
                self.ws = self.wb.active

    def copy_cells(self, src_row_min, src_row_max, count):
        merged_cells = copy(self.get_merged_cells())
        self.logger.debug("detect merged cells: %s" % merged_cells)
        dst_row_base = src_row_max + 1

        for c in range(count):
            for index, row in enumerate(
                self.ws.iter_rows(min_row=src_row_min, max_row=src_row_max)
            ):
                for cell in row:
                    destination = self.ws.cell(row=dst_row_base + index, column=cell.column)
                    destination.value = (
                        Translator(cell.value, origin=cell.coordinate).translate_formula(
                            destination.coordinate
                        )
                        if cell.data_type == "f"
                        else copy(cell.value)
                    )
                    for style in (
                        "font",
                        "fill",
                        "border",
                        "alignment",
                        "protection",
                        "number_format",
                    ):
                        setattr(destination, style, copy(getattr(cell, style)))
                    self.ws.row_dimensions[dst_row_base + index].height = copy(
                        (
                            self.ws.row_dimensions[cell.row].height
                            or self.ws.sheet_format.defaultRowHeight
                            or 15
                        )
                    )

            self.logger.debug("re-merge cells processing start.")
            for merged_cell in copy(merged_cells):
                if merged_cell.min_row < src_row_min or merged_cell.max_row > src_row_max:
                    continue
                self.logger.debug("target base merged cell: %s" % merged_cell)
                align = merged_cell.min_row - src_row_min
                self.logger.debug(
                    "calcurated alignment (merged cell base row - page break row): %s" % align
                )
                _merged_cell = copy(merged_cell)
                _merged_cell.max_row = (
                    dst_row_base + align + (merged_cell.max_row - merged_cell.min_row)
                )
                _merged_cell.min_row = dst_row_base + align
                self.logger.debug(
                    "cell.min_row: %s, cell.max_row: %s, dst_row_base: %d, index: %d, diff: %d"
                    % (
                        merged_cell.min_row,
                        merged_cell.max_row,
                        dst_row_base,
                        index,
                        (merged_cell.max_row - merged_cell.min_row),
                    )
                )
                self.ws.merge_cells(_merged_cell.coord)
                self.logger.debug("merge it: _merged_cell.coord")

            dst_row_base = dst_row_base + index + 1
            self.logger.debug("updated dst_row_base: %s" % dst_row_base)

    def get_merged_cells(self):
        # if "merged_cells_ranges" not in locals():
        #  self.merged_cells_ranges = self.ws.merged_cells.ranges
        return self.ws.merged_cells.ranges

    def get_merged_cell(self, cell):
        for cell_range in self.get_merged_cells():
            for row in list(rows_from_range(str(cell_range))):
                if cell.coordinate in row:
                    return cell_range

    def get_cell_height(self, cell):
        cell_range = self.get_merged_cell(cell)
        if cell_range:
            height = 0
            for row in list(rows_from_range(str(cell_range))):
                height += (
                    self.ws.row_dimensions[self.ws[row[0]].row].height
                    or self.ws.sheet_format.defaultRowHeight
                    or 15
                )
            else:
                return round(height)
        else:
            return round(
                (
                    self.ws.row_dimensions[cell.row].height
                    or self.ws.sheet_format.defaultRowHeight
                    or 15
                )
            )

    def get_cell_height_list(self, cell, to_px=False):
        ret = []
        cell_range = self.get_merged_cell(cell)
        if cell_range:
            for row in list(rows_from_range(str(cell_range))):
                ret.append(
                    (
                        self.ws.row_dimensions[self.ws[row[0]].row].height
                        or self.ws.sheet_format.defaultRowHeight
                        or 15
                    )
                )
        else:
            ret.append(
                (
                    self.ws.row_dimensions[cell.row].height
                    or self.ws.sheet_format.defaultRowHeight
                    or 15
                )
            )

        if to_px:
            ret = [round(height * 1.33) for height in ret]
        return ret

    def get_cell_width(self, cell):
        cell_range = self.get_merged_cell(cell)
        if cell_range:
            width = 0
            for col in list(cols_from_range(str(cell_range))):
                width += self.ws.column_dimensions[get_column_letter(self.ws[col[0]].column)].width
            else:
                return round(width)
        else:
            return round(self.ws.column_dimensions[get_column_letter(cell.column)].width)

    def get_cell_width_list(self, cell, to_px=False):
        ret = []
        cell_range = self.get_merged_cell(cell)
        if cell_range:
            for col in list(cols_from_range(str(cell_range))):
                ret.append(
                    self.ws.column_dimensions[get_column_letter(self.ws[col[0]].column)].width
                )
        else:
            ret.append(self.ws.column_dimensions[get_column_letter(cell.column)].width)

        if to_px:
            ret = [round(width * 8.08) for width in ret]
        return ret

    def get_cell_height_px(self, cell):
        return round(self.get_cell_height(cell) * 1.33)

    def get_cell_width_px(self, cell):
        return round(self.get_cell_width(cell) * 8.08)

    def calc_img_anchor(self, cell, img):
        ## 対象セルのwidth / heightを取得
        width_list = self.get_cell_width_list(cell, to_px=True)
        height_list = self.get_cell_height_list(cell, to_px=True)
        self.logger.debug("width_list: %s" % width_list)
        self.logger.debug("height_list: %s" % height_list)

        img_width = img.width
        img_height = img.height
        self.logger.debug("img_width: %s" % img_width)
        self.logger.debug("img_height: %s" % img_height)

        ## imgのwidth / heightを使って、+何セルかを計算
        for i, cell_width in enumerate(width_list):
            if 0 < (img_width - cell_width):
                img_width -= cell_width
                self.logger.debug("calclating...: %s" % img_width)
            else:
                break
        horizontal_cell_shift = i - 1
        self.logger.debug("horizontal_cell_shift: %s" % horizontal_cell_shift)

        for i, cell_height in enumerate(height_list):
            if 0 < (img_height - cell_height):
                img_height -= cell_height
            else:
                break
        vertical_cell_shift = i - 1
        self.logger.debug("vertical_cell_shift: %s" % vertical_cell_shift)

        ## 渡されたcellの縦横に足す
        anchor_cell_column = cell.column + horizontal_cell_shift
        anchor_cell_row = cell.row + vertical_cell_shift

        ## 余りをEMUに変換
        anchor_cell_column_offset = pixels_to_EMU(img_width)
        anchor_cell_row_offset = pixels_to_EMU(img_height)

        self.logger.debug("anchor_cell_column: %s" % anchor_cell_column)
        self.logger.debug("anchor_cell_column_offset: %s" % anchor_cell_column_offset)
        self.logger.debug("anchor_cell_row: %s" % anchor_cell_row)
        self.logger.debug("anchor_cell_row_offset: %s" % anchor_cell_row_offset)

        ## anchorのcellとoffsetをreturn
        return (
            anchor_cell_column,
            anchor_cell_column_offset,
            anchor_cell_row,
            anchor_cell_row_offset,
        )

    def save(self, filename):
        return self.wb.save(filename)

    def open_image(self, filename):
        return Image.open(filename)

    def save_image(self, img, filename):
        return img.save(filename)

    def resize_image(self, img, width, height):
        return img.resize((max(1, width), max(1, height)), Image.Resampling.LANCZOS)

    def resize_image_to_cell(self, img, cell):
        ratio = 1
        width = img.width
        height = img.height

        font = Font(
            name="Arial",
            sz=11,
        )
        cell.font = font
        # self.wb._named_styles["標準"].font = font

        cellHeight = self.get_cell_height_px(cell)
        cellWidth = self.get_cell_width_px(cell)
        self.logger.debug("target cell width, height: {}, {}".format(cellWidth, cellHeight))

        if width > cellWidth:
            ratio = cellWidth / width
            width *= ratio
            height *= ratio

        if height > cellHeight:
            ratio = cellHeight / height
            height *= ratio
            width *= ratio

        self.logger.debug("needs resize cell width, height: {}, {}".format(width, height))
        return self.resize_image(img, round(width), round(height))

    def add_image(self, filename, cell, adjust=True, workspace="/tmp"):
        self.logger.debug("add image({}) to cell({})".format(filename, cell))
        # Keep transformed pixels in memory until openpyxl saves the workbook.
        # This also avoids leaking resized files into a reused Lambda /tmp directory.
        if adjust:
            with Image.open(filename) as source:
                oriented = ImageOps.exif_transpose(source)
                resized = self.resize_image_to_cell(oriented, cell)
                buffer = BytesIO()
                resized.save(buffer, format="PNG")
                resized.close()
                oriented.close()
            buffer.seek(0)
            img = openpyxl.drawing.image.Image(buffer)
        else:
            img = openpyxl.drawing.image.Image(filename)
        cell.value = ""

        to_cell_column, to_cell_column_offset, to_cell_row, to_cell_row_offset = (
            self.calc_img_anchor(cell, img)
        )

        anchor = TwoCellAnchor(
            _from=AnchorMarker(row=cell.row - 1, col=cell.column - 1),
            to=AnchorMarker(
                row=to_cell_row,
                rowOff=to_cell_row_offset,
                col=to_cell_column,
                colOff=to_cell_column_offset,
            ),
            editAs="twoCell",
        )
        return self.ws.add_image(img, anchor)

    def add_value(self, value, cell):
        if value:
            self.logger.debug("add value({}) to cell({})".format(value, cell))
        cell.value = value
        if isinstance(value, str):
            cell.data_type = "s"  # User text beginning with = is not an Excel formula.
        return cell

    def get_page_breaks(self, row_num_only=False):
        breaks = self.ws.row_breaks.brk
        return [item.id for item in breaks] if row_num_only else breaks

    def add_page_break(self, cell=None, row=None):
        row_number = cell.row if cell is not None else row
        if row_number is None or row_number < 1:
            raise ValueError("A positive page-break row is required")
        self.ws.row_breaks.append(Break(id=row_number))

    def delete_page_break(self, row):
        self.ws.row_breaks.brk = [item for item in self.ws.row_breaks.brk if item.id != row]

    def collect_cells(self, value):
        cells = []
        for row in self.ws:
            for cell in row:
                if cell.value == value:
                    cells.append(cell)
        else:
            return cells

    def aggregate_cell_values(self, max_cnt):
        cell_values: dict[object, list] = {}
        pass_cnt = 0

        for row in self.ws:
            for cell in row:
                if cell.value:
                    if cell.value not in cell_values:
                        cell_values[cell.value] = []
                    elif len(cell_values[cell.value]) > max_cnt:
                        pass_cnt += 1
                        continue
                    cell_values[cell.value].append(cell)
            if pass_cnt > 50:
                break
        return cell_values

    def add_each_images(self, images, cells, clean=True):
        for index, image in enumerate(images):
            self.add_image(image, cells[index])
        else:
            if clean:
                for cell in cells:
                    self.add_value("", cell)
            return cells[index]

    def add_each_values(self, values, cells, clean=True):
        for index, value in enumerate(values):
            self.add_value(value, cells[index])
        else:
            if clean:
                for cell in cells:
                    self.add_value("", cell)
            return cells[index]

    def has_value(self, value):
        if value in self.aggregate_cell_values(1):
            return True
        else:
            return False

    def has_name(self):
        return self.has_value("{name}")

    def has_photo(self):
        return self.has_value("{photo}")

    def has_type(self):
        return self.has_value("{type}")

    def has_title(self):
        return self.has_value("{title}")

    def has_hierarchy(self):
        return self.has_value("{hierarchy}")

    def has_date(self):
        return self.has_value("{date}")

    def has_cell(self, cell, min_row, max_row):
        for _cell in [
            _cell for row in self.ws.iter_rows(min_row=min_row, max_row=max_row) for _cell in row
        ]:
            if _cell == cell:
                return True
        return False

    def get_print_area(self):
        if not self.ws.print_area:
            return None
        areas = list(PrintArea.from_string(self.ws.print_area).ranges)
        if len(areas) != 1:
            raise ValueError("Templates must define exactly one rectangular print area")
        return str(areas[0])

    def get_print_area_first_row(self):
        area = self.get_print_area()
        if area is None:
            raise ValueError("Template has no print area")
        return range_boundaries(area)[1]

    def get_print_area_last_row(self):
        area = self.get_print_area()
        if area is None:
            raise ValueError("Template has no print area")
        return range_boundaries(area)[3]

    def generate(
        self,
        title,
        items,
        save_filename,
        needs_include_hierarchy=False,
        needs_date=False,
        needs_print_settings=False,
        workspace="/tmp",
    ):
        if not items:
            raise ValueError("At least one photo is required to generate a document")
        self.logger.debug("photo list length: {}".format(len(items)))

        # detect to page_breaks
        page_breaks = self.get_page_breaks(row_num_only=True)
        copy_ranges = {
            "src_row_min": self.get_print_area_first_row(),
            "src_row_max": self.get_print_area_last_row(),
        }
        print_min_col, print_min_row, print_max_col, _ = range_boundaries(self.get_print_area())
        self.logger.debug("detect page_breaks: %s" % page_breaks)

        # calcurate copy cell ranges
        if len(page_breaks) == 0:
            pass
        elif len(page_breaks) == 1:
            copy_ranges["src_row_min"] = page_breaks[0] + 1
            copy_ranges["src_row_max"] = self.get_print_area_last_row()
        else:
            raise Exception
        self.logger.debug("determine copy ranges: %s" % copy_ranges)

        self.copy_cells(**copy_ranges, count=len(items) - 1)
        self.logger.debug("copy ranges done.")
        # TODO: Whether the number of "photo" and "name" is the same.
        # pass

        # 既存の改ページを除去
        for i in page_breaks:
            self.logger.debug("delete to page_break: %s" % i)
            self.delete_page_break(i)

        cell_values = self.aggregate_cell_values(len(items))
        for placeholder, enabled in (
            ("{hierarchy}", needs_include_hierarchy),
            ("{date}", needs_date),
        ):
            if not enabled:
                for cell in cell_values.get(placeholder, []):
                    self.add_value("", cell)
        if "{title}" in cell_values:
            title_cells = cell_values["{title}"]
            self.logger.debug("each cell type length: title_cells: %s" % title_cells)

        image_cells = cell_values["{photo}"]
        type_cells = cell_values["{type}"]
        name_cells = cell_values["{name}"]
        self.logger.debug(
            "each cell type length: image_cells: %s, type_cells: %s, name_cells: %s"
            % (image_cells, type_cells, name_cells)
        )

        if "{index}" in cell_values:
            index_cells = cell_values["{index}"]
            self.logger.debug("each cell type length: index_cells: %s" % index_cells)

        if needs_include_hierarchy and "{hierarchy}" in cell_values:
            hierarchies_cells = cell_values["{hierarchy}"]
            self.logger.debug("each cell type length: hierarchy_cells: %s" % hierarchies_cells)
        if needs_date and "{date}" in cell_values:
            date_cells = cell_values["{date}"]
            self.logger.debug("each cell type length: date_cells: %s" % date_cells)

        images = []
        types = []
        names = []
        dates = []
        hierarchies = []
        for item in items:
            images.append(os.path.join(workspace, item["photo"]))
            types.append(item["type"])
            names.append(item["name"])
            if needs_include_hierarchy and "{hierarchy}" in cell_values:
                hierarchies.append(" / ".join(item["hierarchy"]))
            if needs_date and "{date}" in cell_values:
                dates.append(self.iso8601_to_simple(item["created_at"]))
        indexs = list(range(1, len(names) + 1))

        self.logger.debug("images: {}".format(images))
        cells = []
        if "title_cells" in locals():
            title_list = [title for cell in title_cells]
            self.add_each_values(title_list, title_cells, False)
        cells.append(self.add_each_images(images, image_cells, False))
        cells.append(self.add_each_values(types, type_cells, False))
        cells.append(self.add_each_values(names, name_cells, False))

        if "{index}" in cell_values:
            cells.append(self.add_each_values(indexs, index_cells, False))
        if needs_include_hierarchy and "hierarchies_cells" in locals():
            cells.append(self.add_each_values(hierarchies, hierarchies_cells, False))
        if needs_date and "date_cells" in locals():
            cells.append(self.add_each_values(dates, date_cells, False))

        last_col = max(print_max_col, max(cell.column for cell in cells))
        last_row = max(
            max(cell.row for cell in cells),
            copy_ranges["src_row_max"]
            + (copy_ranges["src_row_max"] - copy_ranges["src_row_min"] + 1) * (len(items) - 1),
        )

        for merged_cell in self.get_merged_cells():
            if last_col < merged_cell.max_col:
                last_col = merged_cell.max_col
            if last_row < merged_cell.max_row:
                last_row = merged_cell.max_row

        self.logger.debug("last_col: {}".format(last_col))
        self.logger.debug("last_row: {}".format(last_row))

        # setting print_area
        self.ws.print_area = (
            f"{get_column_letter(print_min_col)}{print_min_row}:"
            f"{get_column_letter(last_col)}{last_row}"
        )
        self.logger.debug("setting print_area: {}".format(self.ws.print_area))

        # setting header / print title
        if needs_print_settings:
            self.ws.oddHeader.left.text = title
            self.ws.oddHeader.left.size = 14
        # self.ws.oddHeader.left.font = "Tahoma,Bold"
        # ws.oddHeader.left.color = "CC3366"

        # setting fixed row
        # self.ws.print_title_rows = "1:1"
        return self.save(save_filename)

    def iso8601_to_simple(self, iso8601):
        dt = datetime.fromisoformat(iso8601.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt_jst = dt.astimezone(timezone(timedelta(hours=+9)))
        return datetime.strftime(dt_jst, "%Y/%m/%d")
