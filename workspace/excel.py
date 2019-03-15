#!/usr/bin/env python3
import openpyxl
from openpyxl.utils import get_column_letter
#from openpyxl.drawing.image import Image
from PIL import Image

if __name__ == '__main__':
  wb = openpyxl.load_workbook('./sample.xlsx')
  ws = wb['photos']

  for row in ws:
    for cell in row:
        if cell.value == "photo":
          # https://stackoverflow.com/questions/51135083/openpyxl-how-to-iterate-over-sheets-to-add-an-image-inside-each-one
          #img = Image('sample.jpg')
          print(cell.value)
          #print(dir(cell))
          print(ws.row_dimensions[cell.row].height)
          print(ws.column_dimensions[get_column_letter(cell.column)].width)

          height = round(ws.row_dimensions[cell.row].height)
          width = round(ws.column_dimensions[get_column_letter(cell.column)].width * 11.2)
          print(height)
          print(width)

          img = Image.open('sample.jpg')
          img = img.resize((width, height),Image.NEAREST)
          img.save('resize.jpg')

          img = openpyxl.drawing.image.Image('resize.jpg')
          anchor = get_column_letter(cell.column) + str(cell.row)
          print(anchor)
          ws.add_image(img, anchor)

  # 保存
  wb.save('pasted.xlsx')

#
#wb = openpyxl.Workbook()
#ws = wb.worksheets[0]
#img = openpyxl.drawing.image.Image('photo.jpg')
#ws.add_image(img,'F10')
