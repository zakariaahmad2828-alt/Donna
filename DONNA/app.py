from flask import Flask, render_template, request, jsonify
import requests
from supabase import create_client, Client
import uuid
import json
import re
from datetime import datetime, timedelta
from dotenv import load_dotenv
import os
import jwt
from werkzeug.security import generate_password_hash, check_password_hash

# Load environment variables first
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'ajd8f92n3kfjSDF9234lkj23nf9234')

# SUPABASE CREDENTIALS
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_ANON_KEY = os.getenv('SUPABASE_ANON_KEY')
supabase: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

# OPENROUTER API
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

print("\n" + "="*80)
print("🔧 CONFIGURATION LOADED")
print("="*80)
print(f"✅ Supabase URL: {SUPABASE_URL[:30]}...")
print(f"✅ Supabase Key: {SUPABASE_ANON_KEY[:30]}...")
print(f"✅ OpenRouter Key: {'***' + OPENROUTER_API_KEY[-10:] if OPENROUTER_API_KEY else 'MISSING'}")
print(f"✅ Secret Key: {app.secret_key[:20]}...")
print("="*80 + "\n")

# ==================== AUTHENTICATION HELPER ====================

def get_current_user():
    """Extract user from JWT token"""
    auth_header = request.headers.get('Authorization', '')
    if not auth_header:
        print("❌ No Authorization header")
        return None
    if not auth_header.startswith('Bearer '):
        print("❌ Invalid Authorization header format")
        return None
    
    token = auth_header.replace('Bearer ', '').strip()
    if not token:
        print("❌ Empty token")
        return None
    
    try:
        payload = jwt.decode(token, app.secret_key, algorithms=['HS256'])
        print(f"✅ Token decoded for user: {payload.get('username')}")
        return payload
    except jwt.ExpiredSignatureError:
        print("❌ Token expired")
        return None
    except jwt.InvalidTokenError as e:
        print(f"❌ Invalid token: {e}")
        return None
    except Exception as e:
        print(f"❌ Token decode error: {e}")
        return None

# ==================== DONNA AI FUNCTIONS ====================

def get_user_context(user_id):
    """Get complete user context: tasks, events, recent activity"""
    try:
        # Get tasks
        tasks_result = supabase.table('tasks').select('*').eq('user_id', user_id).execute()
        tasks = tasks_result.data or []
        
        # Get upcoming events (next 30 days)
        today = datetime.now().date()
        future_date = today + timedelta(days=30)
        
        events_result = supabase.table('calendar_events')\
            .select('*')\
            .eq('user_id', user_id)\
            .gte('date', str(today))\
            .lte('date', str(future_date))\
            .order('date')\
            .execute()
        events = events_result.data or []
        
        # Format context for DONNA
        active_tasks = [t for t in tasks if not t.get('completed')]
        completed_tasks = [t for t in tasks if t.get('completed')]
        
        context = f"""
CURRENT USER CONTEXT (Private - for your analysis only):
==========================================================
📅 TODAY'S DATE: {today.strftime('%A, %B %d, %Y')}

✅ ACTIVE TASKS ({len(active_tasks)} tasks):
"""
        
        if active_tasks:
            for task in active_tasks[:10]:
                due = f" (Due: {task.get('due_date')})" if task.get('due_date') else ""
                priority = f" [{task.get('priority', 'medium').upper()}]" if task.get('priority') else ""
                context += f"- {task['title']}{priority}{due}\n"
                if task.get('description'):
                    context += f" └─ {task['description'][:100]}\n"
        else:
            context += " (No active tasks - user has a clear schedule!)\n"
        
        context += f"\n✓ COMPLETED TASKS: {len(completed_tasks)} tasks done\n"
        context += f"\n📆 UPCOMING EVENTS ({len(events)} events):\n"
        
        if events:
            for event in events[:10]:
                event_date = event.get('date', '')
                event_time = event.get('time', '')
                time_str = f" at {event_time}" if event_time else ""
                context += f"- {event['title']} on {event_date}{time_str}\n"
                if event.get('description'):
                    context += f" └─ {event['description'][:100]}\n"
        else:
            context += " (No upcoming events scheduled)\n"
        
        context += "\n=========================================================="
        
        return context, {
            'tasks': tasks,
            'active_tasks': active_tasks,
            'completed_tasks': completed_tasks,
            'events': events
        }
    
    except Exception as e:
        print(f"❌ Error getting user context: {e}")
        return "", {'tasks': [], 'active_tasks': [], 'completed_tasks': [], 'events': []}


DONNA_SYSTEM_PROMPT = """You are DONNA, an intelligent and personable AI mission control assistant.

CRITICAL JSON FORMAT RULES:
When the user EXPLICITLY asks you to create tasks or events, output ONE JSON per line in this EXACT format:

FOR TASKS:
{"action": "create_task", "title": "Clean task title here", "description": "", "priority": "medium", "due_date": null}

FOR EVENTS:
{"action": "create_event", "title": "Clean event title here", "date": "2025-12-21", "time": "14:00", "description": ""}

CRITICAL RULES - READ CAREFULLY:
1. ONLY create tasks/events when the user EXPLICITLY asks for them
2. DO NOT create tasks about your own responses or what you're saying
3. DO NOT create tasks for greetings, acknowledgments, or conversational phrases
4. Put JSON on SEPARATE lines BEFORE your friendly response
5. Use CLEAN titles - no quotes, asterisks, brackets, or special formatting
6. Don't include priority/date info IN the title - use the proper JSON fields
7. date format: YYYY-MM-DD
8. time format: HH:MM (24-hour, like "14:00" for 2 PM)
9. priority: "high", "medium", or "low"

EXAMPLES OF WHAT TO CREATE:
✅ User: "Add a task to finish the project" → CREATE: {"action": "create_task", "title": "Finish the project", ...}
✅ User: "Remind me to buy groceries" → CREATE: {"action": "create_task", "title": "Buy groceries", ...}
✅ User: "Schedule a meeting tomorrow at 2pm" → CREATE: {"action": "create_event", "title": "Meeting", "date": "2025-12-23", "time": "14:00", ...}

EXAMPLES OF WHAT NOT TO CREATE:
❌ User: "Hi DONNA" → DO NOT create any tasks
❌ User: "What's on my schedule?" → DO NOT create any tasks
❌ Your response contains "I'll help you" → DO NOT create a task about helping
❌ Your response contains "Let me check" → DO NOT create a task about checking

PERSONALITY:
- Conversational & warm
- Confirm what you created ONLY when you actually create something
- Keep titles clean and simple
- Only output JSON when the user is asking you to create/schedule something

Remember: JSON first (on separate lines), then friendly response. User will NOT see the JSON. NEVER create tasks about your own responses or conversational phrases."""

def get_conversation_memory(user_id, limit=5):
    """Get recent conversation for context"""
    try:
        result = supabase.table('messages')\
            .select('user_message, donna_response')\
            .eq('user_id', user_id)\
            .eq('status', 'completed')\
            .order('created_at', desc=True)\
            .limit(limit)\
            .execute()
        
        memory = []
        for msg in reversed(result.data or []):
            if msg.get('user_message'):
                memory.append({"role": "user", "content": msg['user_message']})
            if msg.get('donna_response'):
                memory.append({"role": "assistant", "content": msg['donna_response']})
        
        return memory
    except:
        return []

def clean_title(title):
    """Remove all special formatting from titles"""
    title = re.sub(r'["\']', '', title)
    title = re.sub(r'\*+', '', title)
    title = re.sub(r'\{[^}]*\}', '', title)
    title = re.sub(r'\[[^\]]*\]', '', title)
    title = re.sub(r'!+$', '', title)
    title = re.sub(r'\s+', ' ', title).strip()
    return title

def parse_donna_actions(text):
    """Extract and clean JSON actions from DONNA's response"""
    actions = []
    print(f"\n{'='*80}")
    print(f"🔍 PARSING AI RESPONSE")
    print(f"{'='*80}")
    
    # Line-by-line JSON parsing
    lines = text.split('\n')
    for i, line in enumerate(lines):
        line = line.strip()
        if line.startswith('{') and '"action"' in line:
            try:
                obj = json.loads(line)
                if "action" in obj:
                    if 'title' in obj:
                        obj['title'] = clean_title(obj['title'])
                    actions.append(obj)
                    print(f" ✅ Line {i}: {obj.get('action')} - {obj.get('title', 'N/A')}")
            except json.JSONDecodeError:
                continue
    
    if len(actions) == 0:
        print("📋 Using regex fallback")
        pattern = r'\{[^{}]*"action"[^{}]*\}'
        matches = re.findall(pattern, text)
        for match in matches:
            try:
                obj = json.loads(match)
                if "action" in obj:
                    if 'title' in obj:
                        obj['title'] = clean_title(obj['title'])
                    actions.append(obj)
            except:
                continue
    
    print(f"📊 FINAL: {len(actions)} actions parsed")
    print(f"{'='*80}\n")
    return actions

def execute_donna_action(action, user_id):
    """Execute actions with detailed logging"""
    try:
        action_type = action.get("action")
        print(f"\n🎯 EXECUTING: {action_type}")
        
        if action_type == "create_task":
            task_data = {
                "user_id": user_id,
                "title": clean_title(action.get("title", "Untitled Task")),
                "description": action.get("description", ""),
                "priority": action.get("priority", "medium"),
                "due_date": action.get("due_date"),
                "completed": False,
                "created_at": datetime.utcnow().isoformat()
            }
            
            result = supabase.table("tasks").insert(task_data).execute()
            if result.data:
                print(f"✅ Task created: {result.data[0].get('title')}")
            else:
                print(f"❌ Task creation failed")
        
        elif action_type == "create_event":
            date = action.get("date", datetime.now().strftime("%Y-%m-%d"))
            time = action.get("time", "00:00")
            
            event_data = {
                "user_id": user_id,
                "title": clean_title(action.get("title", "Untitled Event")),
                "description": action.get("description", ""),
                "date": date,
                "time": time,
                "start_time": f"{date}T{time}:00",
                "end_time": f"{date}T{time}:00",
                "created_at": datetime.utcnow().isoformat()
            }
            
            result = supabase.table("calendar_events").insert(event_data).execute()
            if result.data:
                print(f"✅ Event created: {result.data[0].get('title')}")
            else:
                print(f"❌ Event creation failed")
        
        elif action_type == "complete_task":
            task_id = action.get("task_id")
            supabase.table("tasks").update({
                "completed": True
            }).eq("id", task_id).eq("user_id", user_id).execute()
            print(f"✅ Task completed: {task_id}")
        
        elif action_type == "delete_task":
            supabase.table("tasks").delete()\
                .eq("id", action.get("task_id"))\
                .eq("user_id", user_id).execute()
            print(f"✅ Task deleted")
        
        elif action_type == "delete_event":
            supabase.table("calendar_events").delete()\
                .eq("id", action.get("event_id"))\
                .eq("user_id", user_id).execute()
            print(f"✅ Event deleted")
    
    except Exception as e:
        print(f"❌ Action execution error: {e}")
        import traceback
        traceback.print_exc()

# ==================== AUTHENTICATION ROUTES ====================

@app.route('/api/auth/register', methods=['POST'])
def register():
    """Register new user"""
    try:
        data = request.json or {}
        username = data.get('username', '').strip()
        email = data.get('email', '').strip()
        password = data.get('password', '').strip()
        
        print(f"\n📝 Registration attempt: {username}")
        
        if not all([username, email, password]):
            return jsonify({'success': False, 'message': 'Missing required fields'}), 400
        
        if len(password) < 6:
            return jsonify({'success': False, 'message': 'Password must be at least 6 characters'}), 400
        
        # Check if email exists
        existing = supabase.table('users').select('*').eq('email', email).execute()
        if existing.data:
            return jsonify({'success': False, 'message': 'Email already registered'}), 400
        
        # Check if username exists
        existing = supabase.table('users').select('*').eq('username', username).execute()
        if existing.data:
            return jsonify({'success': False, 'message': 'Username already taken'}), 400
        
        # Create user
        password_hash = generate_password_hash(password)
        result = supabase.table('users').insert({
            'email': email,
            'username': username,
            'password_hash': password_hash,
            'created_at': datetime.utcnow().isoformat()
        }).execute()
        
        print(f"✅ User registered: {username}")
        return jsonify({'success': True, 'message': 'Registration successful!'}), 201
    
    except Exception as e:
        print(f"❌ Registration error: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/auth/login', methods=['POST'])
def login():
    """Login user"""
    try:
        data = request.json or {}
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        
        print(f"\n🔐 Login attempt: {username}")
        
        if not username or not password:
            return jsonify({'success': False, 'message': 'Missing credentials'}), 400
        
        # Try to find user by username first
        result = supabase.table('users').select('*').eq('username', username).execute()
        
        # If not found, try email
        if not result.data:
            result = supabase.table('users').select('*').eq('email', username).execute()
        
        if not result.data:
            print(f"❌ User not found: {username}")
            return jsonify({'success': False, 'message': 'Invalid username or password'}), 401
        
        user = result.data[0]
        
        # Check password
        if not check_password_hash(user['password_hash'], password):
            print(f"❌ Invalid password for: {username}")
            return jsonify({'success': False, 'message': 'Invalid username or password'}), 401
        
        # Generate JWT token
        token = jwt.encode({
            'user_id': str(user['id']),
            'username': user['username'],
            'email': user['email'],
            'iat': datetime.utcnow(),
            'exp': datetime.utcnow() + timedelta(days=30)
        }, app.secret_key, algorithm='HS256')
        
        print(f"✅ User logged in: {user['username']}")
        print(f"✅ Token generated: {token[:30]}...")
        
        return jsonify({
            'success': True,
            'token': token,
            'username': user['username'],
            'user': {
                'id': str(user['id']),
                'username': user['username'],
                'email': user['email']
            }
        }), 200
    
    except Exception as e:
        print(f"❌ Login error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)}), 500

# ==================== PAGE ROUTES - NO AUTH CHECK ====================
# These routes just serve HTML - JavaScript handles authentication

@app.route('/')
def index():
    """Serve home page"""
    return render_template('index.html')

@app.route('/login')
def login_page():
    """Serve login page - NO AUTH REQUIRED ON ROUTE"""
    return render_template('login.html')

@app.route('/tasks')
def tasks_page():
    """Serve tasks page"""
    return render_template('tasks.html')

@app.route('/calendar')
def calendar_page():
    """Serve calendar page"""
    return render_template('calendar.html')

# ==================== CHAT ROUTES ====================

@app.route('/api/chat', methods=['POST'])
def chat():
    """Intelligent DONNA chat"""
    try:
        user = get_current_user()
        if not user:
            print("❌ Chat: Unauthorized")
            return jsonify({'success': False, 'error': 'Unauthorized'}), 401
        
        user_id = str(user.get('user_id'))
        data = request.json or {}
        user_message = data.get('message', '')
        
        if not user_message:
            return jsonify({'success': False, 'error': 'Empty message'}), 400
        
        request_id = str(uuid.uuid4())
        
        print(f"\n{'='*80}")
        print(f"💬 NEW CHAT REQUEST")
        print(f"User: {user.get('username')} ({user_id})")
        print(f"Message: {user_message}")
        print(f"{'='*80}")
        
        # Store message
        supabase.table('messages').insert({
            'request_id': request_id,
            'user_id': user_id,
            'user_message': user_message,
            'donna_response': None,
            'status': 'processing'
        }).execute()
        
        # Get context
        user_context, context_data = get_user_context(user_id)
        conversation_memory = get_conversation_memory(user_id, limit=5)
        
        # Build messages
        messages = [
            {"role": "system", "content": DONNA_SYSTEM_PROMPT},
            {"role": "system", "content": user_context},
            *conversation_memory,
            {"role": "user", "content": user_message}
        ]
        
        # Call AI
        print("📡 Calling OpenRouter API...")
        response = requests.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "deepseek/deepseek-chat",
                "messages": messages,
                "temperature": 0.7,
                "max_tokens": 2000,
            },
            timeout=60
        )
        
        if response.status_code != 200:
            raise Exception(f"API error: {response.status_code} - {response.text}")
        
        ai_response = response.json()['choices'][0]['message']['content']
        print(f"✅ AI response received ({len(ai_response)} chars)")
        
        # Parse and execute actions
        actions = parse_donna_actions(ai_response)
        
        print(f"\n🎬 EXECUTING {len(actions)} ACTIONS")
        for i, action in enumerate(actions):
            execute_donna_action(action, user_id)
        
        # Clean response - remove JSON
        clean_response = ai_response
        lines = clean_response.split('\n')
        clean_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('{') and '"action"' in stripped:
                continue
            if stripped.startswith('```json') or stripped.startswith('```'):
                continue
            if stripped:
                clean_lines.append(line)
        
        clean_response = '\n'.join(clean_lines).strip()
        clean_response = re.sub(r'\{[^{}]*"action"[^{}]*\}', '', clean_response)
        clean_response = re.sub(r'\n{3,}', '\n\n', clean_response).strip()
        
        # Store response
        supabase.table('messages').update({
            'donna_response': clean_response,
            'status': 'completed'
        }).eq('request_id', request_id).execute()
        
        print(f"✅ Chat completed - {len(actions)} actions executed")
        
        return jsonify({
            'success': True,
            'requestId': request_id,
            'response': clean_response,
        }), 200
    
    except Exception as e:
        print(f"❌ CHAT ERROR: {e}")
        import traceback
        traceback.print_exc()
        
        try:
            supabase.table('messages').update({
                'status': 'error',
                'donna_response': f'Sorry, I encountered an error: {str(e)}'
            }).eq('request_id', request_id).execute()
        except:
            pass
        
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/chat/history', methods=['GET'])
def get_chat_history():
    """Get chat history"""
    try:
        user = get_current_user()
        if not user:
            print("❌ Chat history: Unauthorized")
            return jsonify({'success': False, 'error': 'Unauthorized'}), 401
        
        user_id = str(user.get('user_id'))
        
        result = supabase.table('messages')\
            .select('*')\
            .eq('user_id', user_id)\
            .eq('status', 'completed')\
            .order('created_at', desc=False)\
            .limit(50)\
            .execute()
        
        messages = []
        for msg in result.data:
            messages.append({
                'user_message': msg.get('user_message'),
                'donna_response': msg.get('donna_response')
            })
        
        print(f"✅ Chat history: {len(messages)} messages for {user.get('username')}")
        return jsonify({'success': True, 'messages': messages}), 200
    
    except Exception as e:
        print(f"❌ Chat history error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ==================== TASK ROUTES ====================

@app.route('/api/tasks', methods=['GET'])
def get_tasks():
    """Get all tasks"""
    try:
        user = get_current_user()
        if not user:
            print("❌ Get tasks: Unauthorized")
            return jsonify({'success': False, 'error': 'Unauthorized'}), 401
        
        user_id = str(user.get('user_id'))
        
        result = supabase.table('tasks')\
            .select('*')\
            .eq('user_id', user_id)\
            .order('created_at', desc=False)\
            .execute()
        
        print(f"✅ Tasks retrieved: {len(result.data or [])} for {user.get('username')}")
        return jsonify({'success': True, 'tasks': result.data or []}), 200
    
    except Exception as e:
        print(f"❌ Get tasks error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/tasks', methods=['POST'])
def create_task():
    """Create new task"""
    try:
        user = get_current_user()
        if not user:
            return jsonify({'success': False, 'error': 'Unauthorized'}), 401
        
        user_id = str(user.get('user_id'))
        data = request.json or {}
        
        result = supabase.table('tasks').insert({
            'user_id': user_id,
            'title': data.get('title', 'Task'),
            'description': data.get('description', ''),
            'priority': data.get('priority', 'medium'),
            'due_date': data.get('due_date'),
            'completed': False,
            'created_at': datetime.utcnow().isoformat()
        }).execute()
        
        print(f"✅ Task created: {data.get('title')}")
        return jsonify({'success': True, 'task': result.data[0] if result.data else {}}), 201
    
    except Exception as e:
        print(f"❌ Create task error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/tasks/<task_id>', methods=['PUT'])
def update_task(task_id):
    """Update task"""
    try:
        user = get_current_user()
        if not user:
            return jsonify({'success': False, 'error': 'Unauthorized'}), 401
        
        user_id = str(user.get('user_id'))
        data = request.json or {}
        
        supabase.table('tasks').update({
            'completed': data.get('completed', True)
        }).eq('id', task_id).eq('user_id', user_id).execute()
        
        print(f"✅ Task updated: {task_id}")
        return jsonify({'success': True}), 200
    
    except Exception as e:
        print(f"❌ Update task error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/tasks/<task_id>', methods=['DELETE'])
def delete_task(task_id):
    """Delete task"""
    try:
        user = get_current_user()
        if not user:
            return jsonify({'success': False, 'error': 'Unauthorized'}), 401
        
        user_id = str(user.get('user_id'))
        
        supabase.table('tasks').delete().eq('id', task_id).eq('user_id', user_id).execute()
        
        print(f"✅ Task deleted: {task_id}")
        return jsonify({'success': True}), 200
    
    except Exception as e:
        print(f"❌ Delete task error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ==================== CALENDAR ROUTES ====================

@app.route('/api/calendar/events', methods=['GET'])
def get_calendar_events():
    """Get calendar events"""
    try:
        user = get_current_user()
        if not user:
            print("❌ Get events: Unauthorized")
            return jsonify({'success': False, 'error': 'Unauthorized'}), 401
        
        user_id = str(user.get('user_id'))
        
        result = supabase.table('calendar_events')\
            .select('*')\
            .eq('user_id', user_id)\
            .order('date', desc=False)\
            .execute()
        
        print(f"✅ Events retrieved: {len(result.data or [])} for {user.get('username')}")
        return jsonify({'success': True, 'events': result.data or []}), 200
    
    except Exception as e:
        print(f"❌ Get events error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/calendar/events', methods=['POST'])
def create_calendar_event():
    """Create calendar event"""
    try:
        user = get_current_user()
        if not user:
            return jsonify({'success': False, 'error': 'Unauthorized'}), 401
        
        user_id = str(user.get('user_id'))
        data = request.json or {}
        
        date = data.get('date', datetime.now().strftime('%Y-%m-%d'))
        time = data.get('time', '00:00')
        
        result = supabase.table('calendar_events').insert({
            'user_id': user_id,
            'title': data.get('title', 'Event'),
            'description': data.get('description', ''),
            'date': date,
            'time': time,
            'start_time': data.get('start_time', f"{date}T{time}:00"),
            'end_time': data.get('end_time', f"{date}T{time}:00"),
            'created_at': datetime.utcnow().isoformat()
        }).execute()
        
        print(f"✅ Event created: {data.get('title')}")
        return jsonify({'success': True, 'event': result.data[0] if result.data else {}}), 201
    
    except Exception as e:
        print(f"❌ Create event error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/calendar/events/<event_id>', methods=['PUT'])
def update_calendar_event(event_id):
    """Update calendar event"""
    try:
        user = get_current_user()
        if not user:
            return jsonify({'success': False, 'error': 'Unauthorized'}), 401
        
        user_id = str(user.get('user_id'))
        data = request.json or {}
        
        update_data = {}
        if 'title' in data:
            update_data['title'] = data['title']
        if 'description' in data:
            update_data['description'] = data['description']
        if 'date' in data:
            update_data['date'] = data['date']
        if 'time' in data:
            update_data['time'] = data['time']
        if 'start_time' in data:
            update_data['start_time'] = data['start_time']
        if 'end_time' in data:
            update_data['end_time'] = data['end_time']
        
        supabase.table('calendar_events').update(update_data)\
            .eq('id', event_id)\
            .eq('user_id', user_id)\
            .execute()
        
        print(f"✅ Event updated: {event_id}")
        return jsonify({'success': True}), 200
    
    except Exception as e:
        print(f"❌ Update event error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/calendar/events/<event_id>', methods=['DELETE'])
def delete_calendar_event(event_id):
    """Delete calendar event"""
    try:
        user = get_current_user()
        if not user:
            return jsonify({'success': False, 'error': 'Unauthorized'}), 401
        
        user_id = str(user.get('user_id'))
        
        supabase.table('calendar_events').delete()\
            .eq('id', event_id)\
            .eq('user_id', user_id)\
            .execute()
        
        print(f"✅ Event deleted: {event_id}")
        return jsonify({'success': True}), 200
    
    except Exception as e:
        print(f"❌ Delete event error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ==================== HEALTH & DEBUG ROUTES ====================

@app.route('/api/health', methods=['GET'])
@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'success': True,
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat()
    }), 200

@app.errorhandler(404)
def not_found(e):
    return jsonify({'success': False, 'error': 'Not found'}), 404

@app.errorhandler(500)
def internal_error(e):
    return jsonify({'success': False, 'error': 'Internal server error'}), 500

# ==================== RUN APPLICATION ====================

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    debug = os.getenv('FLASK_ENV') == 'development' or os.getenv('FLASK_DEBUG') == 'True'
    
    print("\n" + "="*80)
    print("🚀 DONNA AI ASSISTANT - READY TO LAUNCH")
    print("="*80)
    print(f"🌐 Running on: http://localhost:{port}")
    print(f"🔧 Debug mode: {debug}")
    print(f"📅 Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("")
    print("📋 Available Routes:")
    print(" GET / - Chat Interface")
    print(" GET /login - Login Page")
    print(" GET /tasks - Tasks Page")
    print(" GET /calendar - Calendar Page")
    print("")
    print(" POST /api/auth/register - Register User")
    print(" POST /api/auth/login - Login User")
    print(" POST /api/chat - Chat with DONNA")
    print(" GET /api/tasks - Get Tasks")
    print(" GET /api/calendar/events - Get Events")
    print("")
    print("🔍 All requests will show detailed logs")
    print("="*80 + "\n")
    
    app.run(
        host='0.0.0.0',
        port=port,
        debug=debug
    )
