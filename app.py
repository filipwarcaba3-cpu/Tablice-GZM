import os
from flask import Flask, jsonify, send_from_directory

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=None)

@app.get('/')
def home():
    return send_from_directory(BASE_DIR, 'index.html')

@app.get('/health')
def health():
    return jsonify(status='ok', version='2.0')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
