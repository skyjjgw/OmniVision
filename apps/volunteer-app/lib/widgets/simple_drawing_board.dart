import 'dart:typed_data';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';

class SimpleDrawingBoard extends StatefulWidget {
  final Function(Uint8List?) onSave;
  
  const SimpleDrawingBoard({super.key, required this.onSave});

  @override
  State<SimpleDrawingBoard> createState() => _SimpleDrawingBoardState();
}

class _SimpleDrawingBoardState extends State<SimpleDrawingBoard> {
  final List<List<Offset>> _paths = [];
  
  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Container(
          height: 300,
          width: double.infinity,
          decoration: BoxDecoration(
            border: Border.all(color: Colors.grey),
            color: Colors.white,
          ),
          child: GestureDetector(
            onPanStart: (details) {
              setState(() {
                _paths.add([details.localPosition]);
              });
            },
            onPanUpdate: (details) {
              setState(() {
                _paths.last.add(details.localPosition);
              });
            },
            onPanEnd: (details) {
              // Path finished
            },
            child: CustomPaint(
              painter: _DrawingPainter(_paths),
              size: Size.infinite,
            ),
          ),
        ),
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceEvenly,
          children: [
            TextButton.icon(
              icon: const Icon(Icons.undo),
              label: const Text("撤销"),
              onPressed: _paths.isEmpty ? null : () {
                setState(() {
                  _paths.removeLast();
                });
              },
            ),
            TextButton.icon(
              icon: const Icon(Icons.delete),
              label: const Text("清空"),
              onPressed: _paths.isEmpty ? null : () {
                setState(() {
                  _paths.clear();
                });
              },
            ),
            ElevatedButton.icon(
              icon: const Icon(Icons.check),
              label: const Text("完成绘制"),
              onPressed: () async {
                if (_paths.isEmpty) {
                  widget.onSave(null);
                  return;
                }
                final image = await _renderImage();
                final byteData = await image.toByteData(format: ui.ImageByteFormat.png);
                widget.onSave(byteData?.buffer.asUint8List());
              },
            ),
          ],
        ),
      ],
    );
  }

  Future<ui.Image> _renderImage() async {
    final recorder = ui.PictureRecorder();
    final canvas = Canvas(recorder, Rect.fromPoints(const Offset(0, 0), const Offset(400, 300))); // Approx size
    
    // Draw white background
    final bgPaint = Paint()..color = Colors.white;
    canvas.drawRect(const Rect.fromLTWH(0, 0, 800, 800), bgPaint);
    
    final painter = _DrawingPainter(_paths);
    painter.paint(canvas, const Size(800, 800)); // Render high res
    
    final picture = recorder.endRecording();
    return picture.toImage(800, 600);
  }
}

class _DrawingPainter extends CustomPainter {
  final List<List<Offset>> paths;
  
  _DrawingPainter(this.paths);

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = Colors.black
      ..strokeWidth = 3.0
      ..strokeCap = StrokeCap.round
      ..style = PaintingStyle.stroke;

    for (final path in paths) {
      if (path.isEmpty) continue;
      final pathObj = Path();
      pathObj.moveTo(path.first.dx, path.first.dy);
      for (int i = 1; i < path.length; i++) {
        pathObj.lineTo(path[i].dx, path[i].dy);
      }
      canvas.drawPath(pathObj, paint);
    }
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => true;
}
