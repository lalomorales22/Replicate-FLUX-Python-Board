import os
from flask import Flask, request, render_template_string, redirect, url_for, g, jsonify
from dotenv import load_dotenv
import sqlite3
from flask_socketio import SocketIO, emit
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from datetime import datetime
import requests
import base64
import io
from PIL import Image
import replicate

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'your_secret_key_here')
login_manager = LoginManager(app)
login_manager.login_view = 'login'
socketio = SocketIO(app)

# Set your Replicate API token from environment variable
replicate_api_token = os.getenv('REPLICATE_API_TOKEN')
if not replicate_api_token:
    raise ValueError("REPLICATE_API_TOKEN not found in environment variables")
os.environ["REPLICATE_API_TOKEN"] = replicate_api_token

# Database setup
def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect('message_board.db')
    return db

# Add this function to handle image generation
def generate_image_with_replicate(prompt, aspect_ratio="1:1", width=512, height=512):
    model = "black-forest-labs/flux-1.1-pro"
    
    input_data = {
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,
        "width": width,
        "height": height,
        "output_format": "png",
        "safety_tolerance": 2,
        "prompt_upsampling": False
    }
    
    # Run the model
    output = replicate.run(model, input=input_data)
    
    # Download and return the image
    response = requests.get(output)
    image = Image.open(io.BytesIO(response.content))
    
    # Convert image to base64
    buffered = io.BytesIO()
    image.save(buffered, format="PNG")
    return base64.b64encode(buffered.getvalue()).decode()

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    with app.app_context():
        db = get_db()
        cursor = db.cursor()
        
        # Create tables if they don't exist
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users
            (id INTEGER PRIMARY KEY AUTOINCREMENT,
             username TEXT UNIQUE NOT NULL,
             password TEXT NOT NULL,
             avatar TEXT)
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS messages
            (id INTEGER PRIMARY KEY AUTOINCREMENT,
             user_id INTEGER,
             content TEXT NOT NULL,
             timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
             FOREIGN KEY (user_id) REFERENCES users (id))
        ''')
        
        # Check if image_data column exists in messages table
        cursor.execute("PRAGMA table_info(messages)")
        columns = [column[1] for column in cursor.fetchall()]
        
        # Add image_data column if it doesn't exist
        if 'image_data' not in columns:
            cursor.execute('ALTER TABLE messages ADD COLUMN image_data TEXT')
        
        # Create other tables (comments, tags, message_tags, reactions) as before
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS comments
            (id INTEGER PRIMARY KEY AUTOINCREMENT,
             user_id INTEGER,
             message_id INTEGER,
             content TEXT NOT NULL,
             timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
             FOREIGN KEY (user_id) REFERENCES users (id),
             FOREIGN KEY (message_id) REFERENCES messages (id))
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tags
            (id INTEGER PRIMARY KEY AUTOINCREMENT,
             name TEXT UNIQUE NOT NULL)
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS message_tags
            (message_id INTEGER,
             tag_id INTEGER,
             FOREIGN KEY (message_id) REFERENCES messages (id),
             FOREIGN KEY (tag_id) REFERENCES tags (id),
             PRIMARY KEY (message_id, tag_id))
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS reactions
            (id INTEGER PRIMARY KEY AUTOINCREMENT,
             message_id INTEGER,
             user_id INTEGER,
             reaction TEXT,
             FOREIGN KEY (message_id) REFERENCES messages (id),
             FOREIGN KEY (user_id) REFERENCES users (id),
             UNIQUE(message_id, user_id, reaction))
        ''')
        
        db.commit()

init_db()

class User(UserMixin):
    def __init__(self, id, username, avatar):
        self.id = id
        self.username = username
        self.avatar = avatar

@login_manager.user_loader
def load_user(user_id):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    user = cursor.fetchone()
    if user:
        return User(user[0], user[1], user[3])
    return None

@app.route('/')
def index():
    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        SELECT messages.id, messages.content, messages.image_data, messages.timestamp, users.username, users.avatar
        FROM messages
        JOIN users ON messages.user_id = users.id
        ORDER BY messages.timestamp DESC
    ''')
    messages = cursor.fetchall()
    
    for i, message in enumerate(messages):
        cursor.execute('''
            SELECT comments.content, comments.timestamp, users.username, users.avatar
            FROM comments
            JOIN users ON comments.user_id = users.id
            WHERE comments.message_id = ?
            ORDER BY comments.timestamp ASC
        ''', (message[0],))
        comments = cursor.fetchall()
        
        cursor.execute('''
            SELECT tags.name
            FROM tags
            JOIN message_tags ON tags.id = message_tags.tag_id
            WHERE message_tags.message_id = ?
        ''', (message[0],))
        tags = [tag[0] for tag in cursor.fetchall()]
        
        cursor.execute('''
            SELECT reaction, COUNT(*) as count
            FROM reactions
            WHERE message_id = ?
            GROUP BY reaction
        ''', (message[0],))
        reactions = dict(cursor.fetchall())
        
        messages[i] = message + (comments, tags, reactions)
    
    cursor.execute('''
        SELECT tags.name, COUNT(*) as tag_count
        FROM tags
        JOIN message_tags ON tags.id = message_tags.tag_id
        GROUP BY tags.id
        ORDER BY tag_count DESC
        LIMIT 10
    ''')
    popular_tags = cursor.fetchall()
    
    return render_template_string(BASE_HTML, messages=messages, popular_tags=popular_tags)

@app.route('/post_message', methods=['POST'])
@login_required
def post_message():
    content = request.form.get('content')
    tags = request.form.get('tags', '').split(',')
    image_data = request.form.get('image_data')
    
    if content or image_data:
        db = get_db()
        cursor = db.cursor()
        cursor.execute("INSERT INTO messages (user_id, content, image_data) VALUES (?, ?, ?)",
                       (current_user.id, content, image_data))
        message_id = cursor.lastrowid
        
        for tag in tags:
            tag = tag.strip().lower()
            if tag:
                cursor.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag,))
                cursor.execute("SELECT id FROM tags WHERE name = ?", (tag,))
                tag_id = cursor.fetchone()[0]
                cursor.execute("INSERT INTO message_tags (message_id, tag_id) VALUES (?, ?)",
                               (message_id, tag_id))
        
        db.commit()
        
        cursor.execute('''
            SELECT messages.id, messages.content, messages.image_data, messages.timestamp, users.username, users.avatar
            FROM messages
            JOIN users ON messages.user_id = users.id
            WHERE messages.id = ?
        ''', (message_id,))
        new_message = cursor.fetchone()
        
        socketio.emit('new_message', {
            'id': new_message[0],
            'content': new_message[1],
            'image_data': new_message[2],
            'timestamp': new_message[3],
            'username': new_message[4],
            'avatar': new_message[5],
            'tags': tags,
            'reactions': {}
        })
    return redirect(url_for('index'))

@app.route('/generate_image', methods=['POST'])
@login_required
def generate_image():
    prompt = request.form.get('prompt')
    aspect_ratio = request.form.get('aspect_ratio', '1:1')
    width = int(request.form.get('width', 512))
    height = int(request.form.get('height', 512))
    
    try:
        image_data = generate_image_with_replicate(prompt, aspect_ratio, width, height)
        return jsonify({"image_data": image_data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/tag/<tag_name>')
def view_tag(tag_name):
    db = get_db()
    cursor = db.cursor()
    cursor.execute('''
        SELECT messages.id, messages.content, messages.image_data, messages.timestamp, users.username, users.avatar
        FROM messages
        JOIN users ON messages.user_id = users.id
        JOIN message_tags ON messages.id = message_tags.message_id
        JOIN tags ON message_tags.tag_id = tags.id
        WHERE tags.name = ?
        ORDER BY messages.timestamp DESC
    ''', (tag_name,))
    messages = cursor.fetchall()
    
    for i, message in enumerate(messages):
        cursor.execute('''
            SELECT comments.content, comments.timestamp, users.username, users.avatar
            FROM comments
            JOIN users ON comments.user_id = users.id
            WHERE comments.message_id = ?
            ORDER BY comments.timestamp ASC
        ''', (message[0],))
        comments = cursor.fetchall()
        
        cursor.execute('''
            SELECT tags.name
            FROM tags
            JOIN message_tags ON tags.id = message_tags.tag_id
            WHERE message_tags.message_id = ?
        ''', (message[0],))
        tags = [tag[0] for tag in cursor.fetchall()]
        
        cursor.execute('''
            SELECT reaction, COUNT(*) as count
            FROM reactions
            WHERE message_id = ?
            GROUP BY reaction
        ''', (message[0],))
        reactions = dict(cursor.fetchall())
        
        messages[i] = message + (comments, tags, reactions)
    
    return render_template_string(BASE_HTML, messages=messages, current_tag=tag_name)

@app.route('/post_comment/<int:message_id>', methods=['POST'])
@login_required
def post_comment(message_id):
    content = request.form.get('content')
    if content:
        db = get_db()
        cursor = db.cursor()
        cursor.execute("INSERT INTO comments (user_id, message_id, content) VALUES (?, ?, ?)",
                       (current_user.id, message_id, content))
        comment_id = cursor.lastrowid
        db.commit()
        
        cursor.execute('''
            SELECT comments.content, comments.timestamp, users.username, users.avatar
            FROM comments
            JOIN users ON comments.user_id = users.id
            WHERE comments.id = ?
        ''', (comment_id,))
        new_comment = cursor.fetchone()
        
        socketio.emit('new_comment', {
            'message_id': message_id,
            'content': new_comment[0],
            'timestamp': new_comment[1],
            'username': new_comment[2],
            'avatar': new_comment[3]
        })
    return redirect(url_for('index'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        db = get_db()
        cursor = db.cursor()
        cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
        user = cursor.fetchone()
        if user and check_password_hash(user[2], password):
            user_obj = User(user[0], user[1], user[3])
            login_user(user_obj)
            return redirect(url_for('index'))
        return "Invalid username or password"
    return render_template_string(LOGIN_HTML)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        avatar = request.form.get('avatar')
        db = get_db()
        cursor = db.cursor()
        cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
        if cursor.fetchone():
            return "Username already exists"
        cursor.execute("INSERT INTO users (username, password, avatar) VALUES (?, ?, ?)",
                       (username, generate_password_hash(password), avatar))
        db.commit()
        return redirect(url_for('login'))
    return render_template_string(REGISTER_HTML)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/profile/<username>')
def profile(username):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT id, username, avatar FROM users WHERE username = ?", (username,))
    user = cursor.fetchone()
    if user is None:
        return "User not found", 404
    
    cursor.execute('''
        SELECT messages.id, messages.content, messages.image_data, messages.timestamp
        FROM messages
        WHERE messages.user_id = ?
        ORDER BY messages.timestamp DESC
    ''', (user[0],))
    messages = cursor.fetchall()
    
    return render_template_string(PROFILE_HTML, user=user, messages=messages)

@app.route('/add_reaction/<int:message_id>/<reaction>')
@login_required
def add_reaction(message_id, reaction):
    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute('''
            INSERT INTO reactions (message_id, user_id, reaction)
            VALUES (?, ?, ?)
            ON CONFLICT(message_id, user_id, reaction) DO UPDATE SET reaction = excluded.reaction
        ''', (message_id, current_user.id, reaction))
        db.commit()
        
        cursor.execute('''
            SELECT reaction, COUNT(*) as count
            FROM reactions
            WHERE message_id = ?
            GROUP BY reaction
        ''', (message_id,))
        reactions = dict(cursor.fetchall())
        
        socketio.emit('reaction_update', {
            'message_id': message_id,
            'reactions': reactions
        })
        
        return 'OK', 200
    except Exception as e:
        print(f"Error adding reaction: {e}")
        return 'Error', 500

@socketio.on('connect')
def handle_connect():
    print('Client connected')

@socketio.on('disconnect')
def handle_disconnect():
    print('Client disconnected')

BASE_HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Rad Message Board</title>
    <style>
        :root {
            --bg-color: #000;
            --text-color: #fff;
            --border-color: #fff;
            --input-bg-color: #000;
            --input-text-color: #fff;
            --button-bg-color: #fff;
            --button-text-color: #000;
            --tag-bg-color: #fff;
            --tag-text-color: #000;
        }
        body {
            font-family: 'Courier New', monospace;
            background-color: var(--bg-color);
            color: var(--text-color);
            margin: 0;
            padding: 20px;
            transition: background-color 0.3s, color 0.3s;
        }
        .container {
            max-width: 800px;
            margin: 0 auto;
        }
        h1, h2 {
            border-bottom: 4px solid var(--border-color);
            padding-bottom: 10px;
        }
        .message, .comment {
            border: 4px solid var(--border-color);
            padding: 10px;
            margin-bottom: 20px;
        }
        .message-content, .comment-content {
            margin-bottom: 10px;
            word-wrap: break-word;
        }
        .message-meta, .comment-meta {
            font-size: 0.8em;
            color: #ccc;
            margin-bottom: 10px;
        }
        form {
            margin-bottom: 20px;
        }
        input[type="text"], textarea {
            width: calc(100% - 24px);
            padding: 10px;
            margin-bottom: 10px;
            background-color: var(--input-bg-color);
            color: var(--input-text-color);
            border: 2px solid var(--border-color);
        }
        input[type="submit"], button {
            background-color: var(--button-bg-color);
            color: var(--button-text-color);
            border: none;
            padding: 10px 20px;
            cursor: pointer;
        }
        .nav {
            margin-bottom: 20px;
        }
        .nav a {
            color: var(--text-color);
            margin-right: 10px;
        }
        .comments-section {
            margin-top: 10px;
            padding-top: 10px;
            border-top: 2px solid var(--border-color);
        }
        .avatar {
            font-size: 1.5em;
            margin-right: 5px;
        }
        .tag {
            display: inline-block;
            background-color: var(--tag-bg-color);
            color: var(--tag-text-color);
            padding: 2px 5px;
            margin-right: 5px;
            font-size: 0.8em;
        }
        .tag-cloud {
            margin-bottom: 20px;
        }
        #generated-image {
            max-width: 100%;
            height: auto;
            margin-top: 10px;
        }
    </style>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
    <script>
        var socket = io();
        
        function generateImage() {
            var prompt = document.getElementById('image-prompt').value;
            var aspectRatio = document.getElementById('aspect-ratio').value;
            var width = document.getElementById('width').value;
            var height = document.getElementById('height').value;
            fetch('/generate_image', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded',
                },
                body: 'prompt=' + encodeURIComponent(prompt) + 
                      '&aspect_ratio=' + encodeURIComponent(aspectRatio) +
                      '&width=' + encodeURIComponent(width) +
                      '&height=' + encodeURIComponent(height)
            })
            .then(response => response.json())
            .then(data => {
                if (data.error) {
                    alert('Error: ' + data.error);
                } else {
                    document.getElementById('generated-image').src = 'data:image/png;base64,' + data.image_data;
                    document.getElementById('generated-image').style.display = 'block';
                    document.getElementById('image-data').value = data.image_data;
                }
            });
        }
        
        socket.on('new_message', function(message) {
            var messagesContainer = document.querySelector('.container');
            var newMessageElement = document.createElement('div');
            newMessageElement.className = 'message';
            newMessageElement.innerHTML = `
                <div class="message-content">${message.content}</div>
                ${message.image_data ? `<img src="data:image/png;base64,${message.image_data}" alt="Generated Image" style="max-width: 100%; height: auto;">` : ''}
                <div class="message-meta">
                    <span class="avatar">${message.avatar}</span>
                    Posted by ${message.username} on ${message.timestamp}
                </div>
                <div class="message-tags">
                    ${message.tags.map(tag => `<span class="tag">${tag}</span>`).join('')}
                </div>
                <div class="comments-section"></div>
                <form action="/post_comment/${message.id}" method="post">
                    <input type="text" name="content" placeholder="Add a comment" required>
                    <input type="submit" value="Post Comment">
                </form>
            `;
            messagesContainer.insertBefore(newMessageElement, messagesContainer.firstChild);
        });
        
        socket.on('new_comment', function(comment) {
            var messageElement = document.querySelector(`[data-message-id="${comment.message_id}"]`);
            if (messageElement) {
                var commentsSection = messageElement.querySelector('.comments-section');
                var newCommentElement = document.createElement('div');
                newCommentElement.className = 'comment';
                newCommentElement.innerHTML = `
                    <div class="comment-content">${comment.content}</div>
                    <div class="comment-meta">
                        <span class="avatar">${comment.avatar}</span>
                        Posted by ${comment.username} on ${comment.timestamp}
                    </div>
                `;
                commentsSection.appendChild(newCommentElement);
            }
        });

        socket.on('reaction_update', function(data) {
            var messageElement = document.querySelector(`[data-message-id="${data.message_id}"]`);
            if (messageElement) {
                var reactionsElement = messageElement.querySelector('.reactions');
                if (reactionsElement) {
                    for (var reaction in data.reactions) {
                        var button = reactionsElement.querySelector(`[data-reaction="${reaction}"]`);
                        if (button) {
                            button.textContent = `${reaction} ${data.reactions[reaction]}`;
                        }
                    }
                }
            }
        });

        function addReaction(messageId, reaction) {
            fetch(`/add_reaction/${messageId}/${reaction}`, {method: 'GET'})
                .then(response => {
                    if (!response.ok) {
                        throw new Error('Network response was not ok');
                    }
                })
                .catch(error => console.error('Error:', error));
        }
    </script>
</head>
<body>
    <div class="container">
        <div class="nav">
            <a href="{{ url_for('index') }}">Home</a>
            {% if current_user.is_authenticated %}
                <a href="{{ url_for('logout') }}">Logout</a>
                <a href="{{ url_for('profile', username=current_user.username) }}">Profile</a>
            {% else %}
                <a href="{{ url_for('login') }}">Login</a>
                <a href="{{ url_for('register') }}">Register</a>
            {% endif %}
        </div>
        <h1>Rad Message Board</h1>
        {% if popular_tags %}
            <div class="tag-cloud">
                <h2>Popular Tags</h2>
                {% for tag, count in popular_tags %}
                    <a href="{{ url_for('view_tag', tag_name=tag) }}" class="tag">{{ tag }} ({{ count }})</a>
                {% endfor %}
            </div>
        {% endif %}
        {% if current_user.is_authenticated %}
            <form action="{{ url_for('post_message') }}" method="post">
                <textarea name="content" placeholder="What's on your mind?" required></textarea>
                <input type="text" name="tags" placeholder="Tags (comma-separated)">
                <input type="text" id="image-prompt" placeholder="Image generation prompt">
                <select id="aspect-ratio">
                    <option value="1:1">1:1 (Square)</option>
                    <option value="16:9">16:9 (Landscape)</option>
                    <option value="9:16">9:16 (Portrait)</option>
                </select>
                <input type="number" id="width" placeholder="Width (default: 512)" value="512">
                <input type="number" id="height" placeholder="Height (default: 512)" value="512">
                <button type="button" onclick="generateImage()">Generate Image</button>
                <img id="generated-image" src="" alt="Generated Image" style="display:none;">
                <input type="hidden" id="image-data" name="image_data">
                <input type="submit" value="Post Message">
            </form>
        {% endif %}
        {% for message in messages %}
            <div class="message" data-message-id="{{ message[0] }}">
                <div class="message-content">{{ message[1] }}</div>
                {% if message[2] %}
                    <img src="data:image/png;base64,{{ message[2] }}" alt="Generated Image" style="max-width: 100%; height: auto;">
                {% endif %}
                <div class="message-meta">
                    <span class="avatar">{{ message[5] }}</span>
                    Posted by <a href="{{ url_for('profile', username=message[4]) }}">{{ message[4] }}</a> on {{ message[3] }}
                </div>
                {% if message[7] %}
                    <div class="message-tags">
                        {% for tag in message[7] %}
                            <a href="{{ url_for('view_tag', tag_name=tag) }}" class="tag">{{ tag }}</a>
                        {% endfor %}
                    </div>
                {% endif %}
                <div class="reactions">
                    <button onclick="addReaction({{ message[0] }}, '👍')" data-reaction="👍">👍 {{ message[8].get('👍', 0) }}</button>
                    <button onclick="addReaction({{ message[0] }}, '❤️')" data-reaction="❤️">❤️ {{ message[8].get('❤️', 0) }}</button>
                    <button onclick="addReaction({{ message[0] }}, '😂')" data-reaction="😂">😂 {{ message[8].get('😂', 0) }}</button>
                    <button onclick="addReaction({{ message[0] }}, '😮')" data-reaction="😮">😮 {{ message[8].get('😮', 0) }}</button>
                </div>
                {% if message[6] %}
                    <div class="comments-section">
                        <h3>Comments:</h3>
                        {% for comment in message[6] %}
                            <div class="comment">
                                <div class="comment-content">{{ comment[0] }}</div>
                                <div class="comment-meta">
                                    <span class="avatar">{{ comment[3] }}</span>
                                    Posted by <a href="{{ url_for('profile', username=comment[2]) }}">{{ comment[2] }}</a> on {{ comment[1] }}
                                </div>
                            </div>
                        {% endfor %}
                    </div>
                {% endif %}
                {% if current_user.is_authenticated %}
                    <form action="{{ url_for('post_comment', message_id=message[0]) }}" method="post">
                        <input type="text" name="content" placeholder="Add a comment" required>
                        <input type="submit" value="Post Comment">
                    </form>
                {% endif %}
            </div>
        {% endfor %}
    </div>
</body>
</html>
'''

LOGIN_HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Login - Rad Message Board</title>
    <style>
        body {
            font-family: 'Courier New', monospace;
            background-color: #000;
            color: #fff;
            margin: 0;
            padding: 20px;
        }
        .container {
            max-width: 400px;
            margin: 0 auto;
        }
        h1 {
            border-bottom: 4px solid #fff;
            padding-bottom: 10px;
        }
        form {
            border: 4px solid #fff;
            padding: 20px;
        }
        input[type="text"], input[type="password"] {
            width: 100%;
            padding: 10px;
            margin-bottom: 10px;
            background-color: #000;
            color: #fff;
            border: 2px solid #fff;
        }
        input[type="submit"] {
            background-color: #fff;
            color: #000;
            border: none;
            padding: 10px 20px;
            cursor: pointer;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>Login</h1>
        <form action="{{ url_for('login') }}" method="post">
            <input type="text" name="username" placeholder="Username" required>
            <input type="password" name="password" placeholder="Password" required>
            <input type="submit" value="Login">
        </form>
    </div>
</body>
</html>
'''

REGISTER_HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Register - Rad Message Board</title>
    <style>
        body {
            font-family: 'Courier New', monospace;
            background-color: #000;
            color: #fff;
            margin: 0;
            padding: 20px;
        }
        .container {
            max-width: 400px;
            margin: 0 auto;
        }
        h1 {
            border-bottom: 4px solid #fff;
            padding-bottom: 10px;
        }
        form {
            border: 4px solid #fff;
            padding: 20px;
        }
        input[type="text"], input[type="password"], select {
            width: 100%;
            padding: 10px;
            margin-bottom: 10px;
            background-color: #000;
            color: #fff;
            border: 2px solid #fff;
        }
        input[type="submit"] {
            background-color: #fff;
            color: #000;
            border: none;
            padding: 10px 20px;
            cursor: pointer;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>Register</h1>
        <form action="{{ url_for('register') }}" method="post">
            <input type="text" name="username" placeholder="Username" required>
            <input type="password" name="password" placeholder="Password" required>
            <select name="avatar" required>
                <option value="">Select Avatar</option>
                <option value="😊">😊</option>
                <option value="🤠">🤠</option>
                <option value="🤖">🤖</option>
                <option value="👽">👽</option>
                <option value="🦄">🦄</option>
            </select>
            <input type="submit" value="Register">
        </form>
    </div>
</body>
</html>
'''

PROFILE_HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ user[1] }}'s Profile - Rad Message Board</title>
    <style>
        body {
            font-family: 'Courier New', monospace;
            background-color: #000;
            color: #fff;
            margin: 0;
            padding: 20px;
        }
        .container {
            max-width: 800px;
            margin: 0 auto;
        }
        h1, h2 {
            border-bottom: 4px solid #fff;
            padding-bottom: 10px;
        }
        .message {
            border: 4px solid #fff;
            padding: 10px;
            margin-bottom: 20px;
        }
        .message-content {
            margin-bottom: 10px;
            word-wrap: break-word;
        }
        .message-meta {
            font-size: 0.8em;
            color: #ccc;
        }
        .avatar {
            font-size: 2em;
            margin-right: 10px;
        }
        .nav {
            margin-bottom: 20px;
        }
        .nav a {
            color: #fff;
            margin-right: 10px;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="nav">
            <a href="{{ url_for('index') }}">Home</a>
            <a href="{{ url_for('logout') }}">Logout</a>
        </div>
        <h1>{{ user[1] }}'s Profile</h1>
        <p><span class="avatar">{{ user[2] }}</span> {{ user[1] }}</p>
        <h2>Messages</h2>
        {% for message in messages %}
            <div class="message">
                <div class="message-content">{{ message[1] }}</div>
                {% if message[2] %}
                    <img src="data:image/png;base64,{{ message[2] }}" alt="Generated Image" style="max-width: 100%; height: auto;">
                {% endif %}
                <div class="message-meta">Posted on {{ message[3] }}</div>
            </div>
        {% endfor %}
    </div>
</body>
</html>
'''

if __name__ == '__main__':
    socketio.run(app, debug=True)