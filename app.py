# -*- coding: utf-8 -*-
"""
API chấm điểm câu tiếng Trung — Render Free tối ưu.
- Lazy load pycorrector (chỉ load khi request đầu tiên)
- Fallback sang fuzzywuzzy nếu model fail
- RAM < 400MB
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
from fuzzywuzzy import fuzz
import re
import threading

app = Flask(__name__)
CORS(app)

# ═══════════════════════════════════════════════════════
#  GLOBAL STATE — Lazy load
# ═══════════════════════════════════════════════════════
_corrector = None
_corrector_lock = threading.Lock()
_corrector_failed = False


def get_corrector():
    """Lazy load pycorrector — chỉ load 1 lần."""
    global _corrector, _corrector_failed

    if _corrector is not None:
        return _corrector
    if _corrector_failed:
        return None

    with _corrector_lock:
        if _corrector is not None:
            return _corrector
        if _corrector_failed:
            return None

        try:
            print("[API] Loading pycorrector...")
            from pycorrector import MacBertCorrector
            _corrector = MacBertCorrector("shibing624/macbert4csc-base-chinese")
            print("[API] ✅ Loaded")
            return _corrector
        except Exception as e:
            print(f"[API] ❌ Load failed: {e}")
            _corrector_failed = True
            return None


# ═══════════════════════════════════════════════════════
#  HELPER
# ═══════════════════════════════════════════════════════
def clean(text):
    if not text:
        return ''
    return re.sub(r'[。，！？、；：""''「」『』（）《》〈〉【】〔〕\s]+', '', str(text))


def find_diff(user, correct):
    """Tìm lỗi bằng Levenshtein."""
    u = list(clean(user))
    c = list(clean(correct))
    errors = []
    m, n = len(u), len(c)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m + 1): dp[i][0] = i
    for j in range(n + 1): dp[0][j] = j

    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if u[i-1] == c[j-1]:
                dp[i][j] = dp[i-1][j-1]
            else:
                dp[i][j] = 1 + min(dp[i-1][j], dp[i][j-1], dp[i-1][j-1])

    i, j = m, n
    while i > 0 or j > 0:
        if i > 0 and j > 0 and u[i-1] == c[j-1]:
            i -= 1; j -= 1
        elif i > 0 and j > 0 and dp[i][j] == dp[i-1][j-1] + 1:
            errors.append({'type': 'wrong', 'position': j, 'user': u[i-1], 'correct': c[j-1]})
            i -= 1; j -= 1
        elif j > 0 and dp[i][j] == dp[i][j-1] + 1:
            errors.append({'type': 'missing', 'position': j, 'correct': c[j-1]})
            j -= 1
        elif i > 0 and dp[i][j] == dp[i-1][j] + 1:
            errors.append({'type': 'extra', 'position': j, 'user': u[i-1]})
            i -= 1
        else:
            break

    errors.reverse()
    return errors, dp[m][n]


# ═══════════════════════════════════════════════════════
#  GRADE
# ═══════════════════════════════════════════════════════
def grade(user_answer, correct_answer):
    u = clean(user_answer)
    c = clean(correct_answer)

    if not u:
        return {'score': 0, 'status': 'empty', 'message': 'Bạn chưa gõ gì cả',
                'errors': [], 'suggestion': correct_answer}

    # 1. Match chính xác
    if u == c:
        return {'score': 100, 'status': 'correct', 'message': 'Đúng hoàn toàn',
                'errors': [], 'suggestion': None}

    # 2. Dùng pycorrector nếu có
    errors = []
    corrector = get_corrector()
    if corrector:
        try:
            corrected, err_info = corrector.correct(u)
            if err_info:
                for e in err_info:
                    errors.append({
                        'type': 'wrong',
                        'position': e.get('pos', 0),
                        'user': e.get('orig', ''),
                        'correct': e.get('correct', '')
                    })
        except Exception as e:
            print(f"[API] Correct error: {e}")

    # 3. Fallback Levenshtein
    if not errors:
        errors, _ = find_diff(u, c)

    # 4. Tính điểm
    max_len = max(len(u), len(c))
    distance = len(errors)
    ratio = 1 - (distance / max_len) if max_len > 0 else 0
    ratio = max(0, min(1, ratio))

    if ratio >= 0.9:
        status, message = 'correct', 'Đúng (sai nhỏ)'
    elif ratio >= 0.6:
        status, message = 'partial', 'Gần đúng'
    else:
        status, message = 'wrong', 'Sai'

    return {
        'score': round(ratio * 100),
        'status': status,
        'message': message,
        'errors': errors,
        'suggestion': correct_answer if u != c else None,
        'used_ai': corrector is not None
    }


# ═══════════════════════════════════════════════════════
#  ROUTES
# ═══════════════════════════════════════════════════════
@app.route('/health')
def health():
    return jsonify({
        'ok': True,
        'model_loaded': _corrector is not None,
        'model_failed': _corrector_failed
    })


@app.route('/warmup')
def warmup():
    """Ép server load model — dùng cho UptimeRobot."""
    get_corrector()
    return jsonify({'ok': True, 'loaded': _corrector is not None})


@app.route('/check', methods=['POST'])
def check():
    try:
        data = request.get_json(force=True, silent=True) or {}
        u = data.get('user_answer', '')
        c = data.get('correct_answer', '')
        if not c:
            return jsonify({'error': 'Missing correct_answer'}), 400
        return jsonify(grade(u, c))
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/')
def index():
    return jsonify({'name': 'Chinese Grading API', 'version': '3.0-free'})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
