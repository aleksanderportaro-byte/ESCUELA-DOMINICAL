import os
from flask import Flask, render_template, redirect, url_for, session, request, flash
import psycopg
from psycopg.rows import dict_row
from authlib.integrations.flask_client import OAuth

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "clave_secreta_escuela_dominical_2026")

# Credenciales exactas de Google Cloud OAuth que proporcionaste
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")

app.config['GOOGLE_CLIENT_ID'] = GOOGLE_CLIENT_ID
app.config['GOOGLE_CLIENT_SECRET'] = GOOGLE_CLIENT_SECRET

oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)

# Cadena de conexión de Neon DB (Recuerda reemplazar TU_PASSWORD con la contraseña real de tu base de datos)
DATABASE_URL = os.getenv("DATABASE_URL")

def get_db_connection():
    conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
    return conn

# Inicializar Base de Datos en Neon automáticamente
def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Tabla de Asistencia vinculada a cada Clase
    cur.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            id SERIAL PRIMARY KEY,
            class_id INTEGER REFERENCES classes(id) ON DELETE CASCADE,
            student_id INTEGER REFERENCES students(id) ON DELETE CASCADE,
            date DATE NOT NULL,
            status VARCHAR(20) NOT NULL
        );
    """)
    
    # Asegurar que tenga la columna class_id si la tabla ya existía de antes
    try:
        cur.execute("ALTER TABLE attendance ADD COLUMN class_id INTEGER REFERENCES classes(id) ON DELETE CASCADE;")
        conn.commit()
    except Exception:
        conn.rollback()

    # Tabla de Clases
    cur.execute("""
        CREATE TABLE IF NOT EXISTS classes (
            id SERIAL PRIMARY KEY,
            name VARCHAR(150) NOT NULL,
            description TEXT
        );
    """)

    # Tabla de Asignación de Maestros a Clases
    cur.execute("""
        CREATE TABLE IF NOT EXISTS class_teachers (
            id SERIAL PRIMARY KEY,
            class_id INTEGER REFERENCES classes(id) ON DELETE CASCADE,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            assignment_type VARCHAR(20) DEFAULT 'permanent',
            session_date DATE
        );
    """)

    # Tabla de Alumnos
    cur.execute("""
        CREATE TABLE IF NOT EXISTS students (
            id SERIAL PRIMARY KEY,
            first_name VARCHAR(100) NOT NULL,
            last_name VARCHAR(100) NOT NULL
        );
    """)

    # Tabla de Asistencia vinculada a cada Clase
    cur.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            id SERIAL PRIMARY KEY,
            class_id INTEGER REFERENCES classes(id) ON DELETE CASCADE,
            student_id INTEGER REFERENCES students(id) ON DELETE CASCADE,
            date DATE NOT NULL,
            status VARCHAR(20) NOT NULL
        );
    """)
    
    # Tabla de Estrategias de Asistencia
    cur.execute("""
        CREATE TABLE IF NOT EXISTS attendance_strategies (
            id SERIAL PRIMARY KEY,
            title VARCHAR(150) NOT NULL,
            description TEXT NOT NULL,
            status VARCHAR(50) DEFAULT 'En progreso',
            created_at DATE DEFAULT CURRENT_DATE
        );
    """)
    
    conn.commit()
    cur.close()
    conn.close()

try:
    init_db()
except Exception as e:
    print(f"Error al conectar con Neon (Verifica tu contraseña): {e}")

@app.route('/')
def index():
    if 'user' in session:
        return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/login/google')
def login_google():
    redirect_uri = url_for('authorize_google', _external=True)
    return google.authorize_redirect(redirect_uri)

@app.route('/authorize/google')
def authorize_google():
    token = google.authorize_access_token()
    user_info = token.get('user_info')
    if not user_info:
        # Fallback por si la estructura del token varía
        resp = google.get('https://www.googleapis.com/oauth2/v3/userinfo')
        user_info = resp.json()

    google_id = user_info['sub']
    email = user_info['email']
    name = user_info['name']

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT * FROM users WHERE google_id = %s OR email = %s", (google_id, email))
    user = cur.fetchone()

    if not user:
        # Definir rol y posición inicial si es el administrador principal
        if email == 'aleksanderportaro@gmail.com':
            role = 'admin'
            position = 'Secretario (Administrador Principal)'
        else:
            role = 'teacher'
            position = 'Maestro'

        cur.execute("""
            INSERT INTO users (google_id, email, name, role, position) 
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id, google_id, email, name, role, position
        """, (google_id, email, name, role, position))
        user = cur.fetchone()
        conn.commit()
    else:
        # Asegurar que tu correo siempre mantenga privilegios de admin/secretario
        if email == 'aleksanderportaro@gmail.com' and user['role'] != 'admin':
            cur.execute("UPDATE users SET role = 'admin', position = 'Secretario (Administrador Principal)' WHERE email = %s", (email,))
            conn.commit()
            cur.execute("SELECT * FROM users WHERE email = %s", (email,))
            user = cur.fetchone()

    session['user'] = {
        'id': user['id'],
        'email': user['email'],
        'name': user['name'],
        'role': user['role'],
        'position': user.get('position', 'Maestro')
    }

    cur.close()
    conn.close()
    return redirect(url_for('dashboard'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/dashboard')
def dashboard():
    if 'user' not in session:
        return redirect(url_for('index'))
    return render_template('dashboard.html', user=session['user'])

# MÓDULO 1: MATERIALES Y STOCK (Evita cruces entre maestros)
@app.route('/materials', methods=['GET', 'POST'])
def materials():
    if 'user' not in session:
        return redirect(url_for('index'))
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Devolver automáticamente al stock los materiales vencidos si la columna de fecha existe
    try:
        cur.execute("""
            SELECT id, material_id, quantity FROM material_requests 
            WHERE class_date < CURRENT_DATE - INTERVAL '1 day'
        """)
        expired_requests = cur.fetchall()
        for req in expired_requests:
            cur.execute("UPDATE materials SET stock = stock + %s WHERE id = %s", (req['quantity'], req['material_id']))
            cur.execute("DELETE FROM material_requests WHERE id = %s", (req['id'],))
        conn.commit()
    except Exception:
        conn.rollback() # Si la columna tiene otro nombre o no existe, el sistema continúa sin caerse
    
    if request.method == 'POST':
        user_id = session['user']['id']
        
        if 'add_material' in request.form and session['user']['role'] == 'admin':
            mat_name = request.form['mat_name']
            total_stock = int(request.form['total_stock'])
            cur.execute("INSERT INTO materials (name, total_stock) VALUES (%s, %s)", (mat_name, total_stock))
            conn.commit()
            flash('Material agregado al inventario con éxito.', 'success')
            
        elif 'request_material' in request.form:
            material_id = int(request.form['material_id'])
            quantity_requested = int(request.form['quantity'])
            class_date = request.form['class_date']
            
            cur.execute("SELECT total_stock, name FROM materials WHERE id = %s", (material_id,))
            mat = cur.fetchone()
            
            cur.execute(
                "SELECT SUM(quantity) as total_reserved FROM material_requests WHERE material_id = %s AND class_date = %s",
                (material_id, class_date)
            )
            res = cur.fetchone()
            reserved = res['total_reserved'] if res['total_reserved'] else 0
            available_stock = mat['total_stock'] - reserved
            
            if quantity_requested <= available_stock:
                cur.execute(
                    "INSERT INTO material_requests (user_id, material_id, quantity, class_date) VALUES (%s, %s, %s, %s)",
                    (user_id, material_id, quantity_requested, class_date)
                )
                conn.commit()
                flash(f'¡Solicitud exitosa! Se han reservado {quantity_requested} de {mat["name"]} para el {class_date}.', 'success')
            else:
                flash(f'Stock insuficiente para el {class_date}. Disponible: {available_stock}', 'danger')

    cur.execute("SELECT * FROM materials ORDER BY name ASC;")
    materials_list = cur.fetchall()
    
    cur.execute('''
        SELECT mr.id, u.name as teacher_name, m.name as material_name, mr.quantity, mr.class_date, mr.status
        FROM material_requests mr
        JOIN users u ON mr.user_id = u.id
        JOIN materials m ON mr.material_id = m.id
        ORDER BY mr.class_date DESC;
    ''')
    requests_list = cur.fetchall()
    
    cur.close()
    conn.close()
    
    return render_template('materials.html', materials=materials_list, requests=requests_list)

# MÓDULO 2: CALENDARIO DE CLASES DEL MES
@app.route('/schedule', methods=['GET', 'POST'])
def schedule():
    if 'user' not in session:
        return redirect(url_for('index'))
        
    conn = get_db_connection()
    cur = conn.cursor()
    
    if request.method == 'POST' and session['user']['role'] == 'admin':
        month = request.form['month']
        title = request.form['title']
        meeting_link = request.form['meeting_link']
        scheduled_date = request.form['scheduled_date']
        
        cur.execute(
            "INSERT INTO classes_schedule (month, title, meeting_link, scheduled_date) VALUES (%s, %s, %s, %s)",
            (month, title, meeting_link, scheduled_date)
        )
        conn.commit()
        flash('Clase programada con éxito.', 'success')
        
    cur.execute("SELECT * FROM classes_schedule ORDER BY scheduled_date ASC;")
    classes = cur.fetchall()
    
    cur.close()
    conn.close()
    
    return render_template('schedule.html', classes=classes)

# MÓDULO 3: ASISTENCIA EXCLUSIVA DEL ADMIN (Ordenada alfabéticamente por apellido)
@app.route('/admin/attendance', methods=['GET', 'POST'])
def admin_attendance():
    if 'user' not in session or session['user']['role'] != 'admin':
        flash('Acceso restringido solo al Administrador.', 'danger')
        return redirect(url_for('dashboard'))
        
    conn = get_db_connection()
    cur = conn.cursor()
    
    if request.method == 'POST':
        if 'add_student' in request.form:
            first_name = request.form['first_name'].strip()
            last_name = request.form['last_name'].strip()
            clase_id = request.form.get('clase_id') # Capturamos la clase seleccionada
            
            if first_name and last_name:
                # Asegúrate de que tu tabla 'students' tenga la columna 'clase_id'
                cur.execute(
                    "INSERT INTO students (first_name, last_name, clase_id) VALUES (%s, %s, %s)", 
                    (first_name, last_name, clase_id)
                )
                conn.commit()
                flash('Alumno registrado y asignado a la clase correctamente.', 'success')
                
        elif 'save_attendance' in request.form:
            class_date = request.form['class_date']
            cur.execute("SELECT id FROM students;")
            all_students = cur.fetchall()

            for student in all_students:
                s_id = student['id']
                present = f'present_{s_id}' in request.form
                cur.execute('''
                    INSERT INTO attendance (student_id, class_date, present)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (student_id, class_date)
                    DO UPDATE SET present = EXCLUDED.present;
                ''', (s_id, class_date, present))
            conn.commit()
            flash('Asistencia guardada correctamente.', 'success')

    # ORDENADO AUTOMÁTICAMENTE POR APELLIDO (A - Z)
    cur.execute(
        "SELECT id, first_name, last_name FROM students ORDER BY last_name ASC,"
        " first_name ASC;"
    )
    students = cur.fetchall()

    selected_date = request.args.get('date', '')
    attendance_map = {}
    if selected_date:
        cur.execute(
            'SELECT student_id, present FROM attendance WHERE class_date = %s;',
            (selected_date,),
        )
        att_records = cur.fetchall()
        for rec in att_records:
            attendance_map[rec['student_id']] = rec['present']

    # === PEGA AQUÍ EL CÓDIGO PARA TRAER LAS CLASES ===
    cur.execute(
        'SELECT * FROM classes;'
    )  # O 'clases' dependiendo de cómo se llame tu tabla en la base de datos
    clases = cur.fetchall()
    # ================================================

    cur.close()
    conn.close()

    # === MODIFICA ESTA LÍNEA AGREGANDO ", clases=clases" AL FINAL ===
    return render_template(
        'admin_attendance.html',
        students=students,
        selected_date=selected_date,
        attendance_map=attendance_map,
        clases=clases,
    )
    
@app.route('/edit_student_class/<int:student_id>', methods=['POST'])
def edit_student_class(student_id):
    clase_id = request.form.get('clase_id')
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE students SET clase_id = %s WHERE id = %s", (clase_id, student_id))
    conn.commit()
    cur.close()
    conn.close()
    flash('Clase del alumno actualizada con éxito.', 'success')
    return redirect(url_for('admin_attendance'))

@app.route('/delete_student/<int:student_id>', methods=['POST'])
def delete_student(student_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM students WHERE id = %s", (student_id,))
    conn.commit()
    cur.close()
    conn.close()
    flash('Alumno eliminado del registro.', 'success')
    return redirect(url_for('admin_attendance'))

# MÓDULO 4: GESTIÓN DE MAESTROS Y USUARIOS (Exclusivo Administrador)
@app.route('/admin/classes', methods=['GET', 'POST'])
def admin_classes():
    if 'user' not in session or session['user']['role'] != 'admin':
        flash('Acceso restringido.', 'danger')
        return redirect(url_for('dashboard'))

    conn = get_db_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        if 'create_class' in request.form:
            name = request.form['name']
            description = request.form['description']
            cur.execute("INSERT INTO classes (name, description) VALUES (%s, %s)", (name, description))
            conn.commit()
            flash('Clase creada exitosamente.', 'success')

        elif 'assign_teacher' in request.form:
            class_id = request.form['class_id']
            user_id = request.form.get('user_id')
            custom_teacher_name = request.form.get('custom_teacher_name', '').strip()
            assignment_type = request.form['assignment_type']
            session_date = request.form.get('session_date') if assignment_type == 'temporary' else None

            # Si se seleccionó un usuario del desplegable, lo usamos; si está vacío, guardamos el nombre escrito a mano
            u_id = int(user_id) if user_id and user_id.isdigit() else None
            c_name = custom_teacher_name if not u_id else None

            cur.execute("""
                INSERT INTO class_teachers (class_id, user_id, custom_teacher_name, assignment_type, session_date)
                VALUES (%s, %s, %s, %s, %s)
            """, (class_id, u_id, c_name, assignment_type, session_date))
            conn.commit()
            flash('Maestro asignado correctamente.', 'success')

    cur.execute("SELECT * FROM classes ORDER BY name ASC;")
    clases = cur.fetchall()

    cur.execute("SELECT id, name, email FROM users ORDER BY name ASC;")
    teachers = cur.fetchall()

    cur.close()
    conn.close()
    return render_template('admin_classes.html', clases=clases, teachers=teachers)

@app.route('/delete_class/<int:class_id>', methods=['POST'])
def delete_class(class_id):
    if 'user' not in session or session['user']['role'] != 'admin':
        flash('Acceso restringido.', 'danger')
        return redirect(url_for('dashboard'))

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM classes WHERE id = %s", (class_id,))
    conn.commit()
    cur.close()
    conn.close()
    flash('Clase eliminada correctamente.', 'success')
    return redirect(url_for('admin_classes'))


# GESTIÓN DE PERSONAL Y CARGOS (Solo Admin)
@app.route('/admin/teachers', methods=['GET', 'POST'])
def admin_teachers():
    if 'user' not in session or session['user']['role'] != 'admin':
        flash('Acceso restringido solo al Administrador.', 'danger')
        return redirect(url_for('dashboard'))

    conn = get_db_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        if 'update_user' in request.form:
            user_id = request.form['user_id']
            new_role = request.form['role']
            new_position = request.form['position']
            cur.execute("UPDATE users SET role = %s, position = %s WHERE id = %s", (new_role, new_position, user_id))
            conn.commit()
            flash('Datos y cargo del usuario actualizados con éxito.', 'success')

        elif 'delete_user' in request.form:
            user_id = request.form['user_id']
            if int(user_id) != session['user']['id']:
                cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
                conn.commit()
                flash('Usuario eliminado correctamente.', 'success')
            else:
                flash('No puedes eliminar tu propia cuenta de administrador principal.', 'danger')

    cur.execute("SELECT id, name, email, role, position FROM users ORDER BY name ASC;")
    teachers = cur.fetchall()

    cur.close()
    conn.close()
    return render_template('admin_teachers.html', teachers=teachers)

@app.route('/admin/attendance-stats', methods=['GET', 'POST'])
def attendance_stats():
    if 'user' not in session or session['user']['role'] != 'admin':
        flash('Acceso restringido.', 'danger')
        return redirect(url_for('dashboard'))

    conn = get_db_connection()
    cur = conn.cursor()

    if request.method == 'POST' and 'add_strategy' in request.form:
        title = request.form['title']
        description = request.form['description']
        status = request.form['status']
        cur.execute("""
            INSERT INTO attendance_strategies (title, description, status) 
            VALUES (%s, %s, %s)
        """, (title, description, status))
        conn.commit()
        flash('Estrategia registrada con éxito.', 'success')

    # Estadísticas de asistencia agrupadas por fecha y clase (según la estructura actual de tu base de datos)
    try:
        cur.execute("""
            SELECT c.name as class_name, 
                   a.date as week_start,
                   COUNT(CASE WHEN a.status = 'presente' THEN 1 END) as total_presentes,
                   COUNT(a.id) as total_registrados
            FROM attendance a
            JOIN classes c ON a.class_id = c.id
            GROUP BY c.name, a.date
            ORDER BY a.date DESC, c.name ASC;
        """)
        weekly_stats = cur.fetchall()
    except Exception:
        conn.rollback()
        weekly_stats = []

    # Obtener estrategias registradas
    cur.execute("SELECT * FROM attendance_strategies ORDER BY id DESC;")
    strategies = cur.fetchall()

    cur.close()
    conn.close()
    return render_template('attendance_stats.html', weekly_stats=weekly_stats, strategies=strategies)

@app.route('/delete_material_request/<int:req_id>', methods=['POST'])
def delete_material_request(req_id):
    if 'user' not in session or session['user']['email'] != 'aleksanderportaro@gmail.com':
        flash('Acceso no autorizado.', 'danger')
        return redirect(url_for('materials'))

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM material_requests WHERE id = %s", (req_id,))
    conn.commit()
    cur.close()
    conn.close()
    
    flash('Registro eliminado del historial correctamente.', 'success')
    return redirect(url_for('materials'))

@app.route('/clase/<int:clase_id>/asistencia')
def ver_asistencia_clase(clase_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM classes WHERE id = %s", (clase_id,))
    clase = cur.fetchone()
    cur.close()
    conn.close()
    return render_template('attendance_stats.html', clase=clase)

if __name__ == '__main__':
    app.run(debug=True, port=5000)