import os
import io
import uuid
import ffmpeg
from flask import Flask, request, send_file, render_template, jsonify

app = Flask(__name__)
TEMP_DIR = os.path.join(os.getcwd(), 'tmp')
os.makedirs(TEMP_DIR, exist_ok=True)

@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    return response

@app.route('/')
def index():
    return render_template('index.html')

def safe_remove(filepath):
    if filepath and os.path.exists(filepath):
        try:
            os.remove(filepath)
        except Exception:
            pass

def get_ext(filename, default='mp4'):
    if '.' in filename:
        return filename.rsplit('.', 1)[-1].lower()
    return default

@app.route('/burn', methods=['POST'])
def burn():
    if 'clip_a' not in request.files or 'clip_b' not in request.files:
        return jsonify({"error": "missing files"}), 400

    clip_a = request.files['clip_a']
    clip_b = request.files['clip_b']
    duration = float(request.form.get('duration', 1.5))
    style = request.form.get('style', 'fadewhite')

    temp_files = []
    try:
        path_a = os.path.join(TEMP_DIR, f"{uuid.uuid4()}.{get_ext(clip_a.filename)}")
        path_b = os.path.join(TEMP_DIR, f"{uuid.uuid4()}.{get_ext(clip_b.filename)}")
        out_path = os.path.join(TEMP_DIR, f"{uuid.uuid4()}.mp4")
        temp_files.extend([path_a, path_b, out_path])

        clip_a.save(path_a)
        clip_b.save(path_b)

        probe = ffmpeg.probe(path_a)
        duration_a = float(probe['format'].get('duration', 5.0))
        offset = max(0.1, duration_a - duration)

        # Scale and pad both inputs identically to avoid xfade crashes
        a_vid = ffmpeg.input(path_a).video \
            .filter('scale', 1280, 720, force_original_aspect_ratio='decrease') \
            .filter('pad', 1280, 720, '(ow-iw)/2', '(oh-ih)/2', color='black') \
            .filter('setsar', 1).filter('fps', fps=30)
            
        b_vid = ffmpeg.input(path_b).video \
            .filter('scale', 1280, 720, force_original_aspect_ratio='decrease') \
            .filter('pad', 1280, 720, '(ow-iw)/2', '(oh-ih)/2', color='black') \
            .filter('setsar', 1).filter('fps', fps=30)

        joined = ffmpeg.filter([a_vid, b_vid], 'xfade', transition=style, duration=duration, offset=offset)

        # Safely map audio if it exists in clip A
        has_audio = any(s.get('codec_type') == 'audio' for s in probe.get('streams', []))
        if has_audio:
            a_aud = ffmpeg.input(path_a).audio
            out = ffmpeg.output(joined, a_aud, out_path, vcodec='libx264', preset='ultrafast', crf=23, acodec='aac', shortest=None)
        else:
            out = ffmpeg.output(joined, out_path, vcodec='libx264', preset='ultrafast', crf=23)

        out.run(capture_stdout=True, capture_stderr=True)

        with open(out_path, 'rb') as f:
            video_data = f.read()

        return send_file(io.BytesIO(video_data), mimetype='video/mp4', as_attachment=True, download_name='burn_transition.mp4')

    except ffmpeg.Error as e:
        err_msg = e.stderr.decode('utf8', errors='ignore') if e.stderr else str(e)
        return jsonify({"error": "processing failed", "detail": err_msg}), 500
    except Exception as e:
        return jsonify({"error": "processing failed", "detail": str(e)}), 500
    finally:
        for f in temp_files:
            safe_remove(f)

@app.route('/freeze', methods=['POST'])
def freeze():
    if 'subject' not in request.files:
        return jsonify({"error": "missing subject file"}), 400
        
    bgs = [request.files[k] for k in request.files if k.startswith('bg_')]
    if not bgs:
        return jsonify({"error": "missing background files"}), 400

    subject = request.files['subject']
    frames_per_bg = int(request.form.get('frames_per_bg', 3))
    duration = int(request.form.get('duration', 3))

    fps = 24
    seg_dur = frames_per_bg / fps
    temp_files = []

    try:
        subj_path = os.path.join(TEMP_DIR, f"{uuid.uuid4()}.{get_ext(subject.filename, 'png')}")
        subject.save(subj_path)
        temp_files.append(subj_path)
        
        out_path = os.path.join(TEMP_DIR, f"{uuid.uuid4()}.mp4")
        temp_files.append(out_path)

        # Process backgrounds
        inputs = []
        for i, bg in enumerate(bgs):
            ext = get_ext(bg.filename)
            bg_path = os.path.join(TEMP_DIR, f"{uuid.uuid4()}.{ext}")
            bg.save(bg_path)
            temp_files.append(bg_path)
            
            is_video = ext in ['mp4', 'webm', 'mov', 'avi', 'mkv']
            if is_video:
                inputs.append(ffmpeg.input(bg_path, stream_loop=-1, t=seg_dur).video)
            else:
                inputs.append(ffmpeg.input(bg_path, loop=1, t=seg_dur).video)

        scaled_bgs = []
        for inp in inputs:
            scaled = inp.filter('scale', 1280, 720, force_original_aspect_ratio='decrease') \
                        .filter('pad', 1280, 720, '(ow-iw)/2', '(oh-ih)/2') \
                        .filter('setsar', 1).filter('fps', fps=fps, round='up')
            scaled_bgs.append(scaled)

        # Concat backgrounds
        if len(scaled_bgs) > 1:
            concat_bg = ffmpeg.concat(*scaled_bgs, v=1, a=0)
        else:
            concat_bg = scaled_bgs[0]

        loop_size = len(scaled_bgs) * frames_per_bg
        looped_bg = concat_bg.filter('loop', loop=-1, size=loop_size, start=0).filter('trim', duration=duration)

        # Overlay Subject
        subj_inp = ffmpeg.input(subj_path, loop=1, t=duration)
        scaled_subj = subj_inp.video.filter('scale', 1280, 720, force_original_aspect_ratio='decrease') \
                                    .filter('setsar', 1).filter('fps', fps=fps)

        final = ffmpeg.overlay(looped_bg, scaled_subj, x='(W-w)/2', y='(H-h)/2', format='auto')
        
        out = ffmpeg.output(final, out_path, vcodec='libx264', preset='ultrafast', crf=23, t=duration)
        out.run(capture_stdout=True, capture_stderr=True)

        with open(out_path, 'rb') as f:
            video_data = f.read()

        return send_file(io.BytesIO(video_data), mimetype='video/mp4', as_attachment=True, download_name='subject_freeze.mp4')

    except ffmpeg.Error as e:
        err_msg = e.stderr.decode('utf8', errors='ignore') if e.stderr else str(e)
        return jsonify({"error": "processing failed", "detail": err_msg}), 500
    except Exception as e:
        return jsonify({"error": "processing failed", "detail": str(e)}), 500
    finally:
        for f in temp_files:
            safe_remove(f)

if __name__ == '__main__':
    app.run(debug=True, port=5000)