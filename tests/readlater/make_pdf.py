"""make_pdf.py OUT [--blank]: a tiny valid PDF with a text layer (or, with --blank, a page with no text at all,
like a scan without OCR). Offsets in the xref table are computed, so pdftotext/pdfinfo accept it."""
import sys

out, blank = sys.argv[1], '--blank' in sys.argv
lines = ['Quarterly Report Q3 2026', 'Revenue grew because the router balanced experts across all GPUs.',
         'The Madeira office opened in November and the Zurich team doubled in size this year.',
         'Next steps: quantisation, pruning of late experts, and better benchmarks from real chat traffic.']
if blank:
    stream = b'0 0 1 rg 100 100 200 200 re f'
else:
    ops = ['BT /F1 12 Tf 72 720 Td 16 TL']
    for l in lines:
        ops.append('(' + l.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)') + ') Tj T*')
    ops.append('ET')
    stream = '\n'.join(ops).encode()
objs = [b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>',
        b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
        b'<< /Title (Q3 Report) /Author (Finance Team) >>']
pdf, offs = bytearray(b'%PDF-1.4\n'), []
for i, o in enumerate(objs, 1):
    offs.append(len(pdf))
    pdf += f'{i} 0 obj\n'.encode() + o + b'\nendobj\n'
x = len(pdf)
pdf += f'xref\n0 {len(objs) + 1}\n0000000000 65535 f \n'.encode()
for o in offs:
    pdf += f'{o:010d} 00000 n \n'.encode()
pdf += f'trailer\n<< /Size {len(objs) + 1} /Root 1 0 R /Info 6 0 R >>\nstartxref\n{x}\n%%EOF\n'.encode()
open(out, 'wb').write(pdf)
