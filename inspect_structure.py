from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from docx.oxml.ns import qn

path=r'D:\GK\T\data\t-system\T系统需求规格说明书v5.0.docx'
doc=Document(path)

def iter_blocks(parent):
    body=parent.element.body
    for child in body.iterchildren():
        if child.tag==qn('w:p'): yield Paragraph(child,parent)
        elif child.tag==qn('w:tbl'): yield Table(child,parent)

for i,b in enumerate(iter_blocks(doc)):
    if isinstance(b,Paragraph):
        t=b.text.strip()
        if t: print(f'P{i}: style={b.style.name!r} keepNext={b.paragraph_format.keep_with_next} pageBefore={b.paragraph_format.page_break_before} {t}')
    else:
        print(f'T{i}: {len(b.rows)}x{len(b.columns)}')
        for ri,row in enumerate(b.rows):
            print(' ',ri,[c.text.replace('\n',' / ') for c in row.cells])
