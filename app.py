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
            class_date DATE NOT NULL,
            present BOOLEAN NOT NULL DEFAULT false,
            UNIQUE(student_id, class_date)
        );
    """)
    
    # Asegurar que tenga la columna class_id si la tabla ya existía de antes
    try:
        cur.execute("ALTER TABLE attendance ADD COLUMN class_id INTEGER REFERENCES classes(id) ON DELETE CASCADE;")
        conn.commit()
    except Exception:
        conn.rollback()

    # Asegurar compatibilidad: renombrar columnas antiguas si existen
    try:
        cur.execute("ALTER TABLE attendance RENAME COLUMN date TO class_date;")
        conn.commit()
    except Exception:
        conn.rollback()
    try:
        cur.execute("ALTER TABLE attendance ADD COLUMN class_date DATE;")
        conn.commit()
    except Exception:
        conn.rollback()
    try:
        cur.execute("ALTER TABLE attendance ADD COLUMN present BOOLEAN DEFAULT false;")
        conn.commit()
    except Exception:
        conn.rollback()
    try:
        cur.execute("ALTER TABLE attendance ADD UNIQUE(student_id, class_date);")
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
            custom_teacher_name VARCHAR(150),
            assignment_type VARCHAR(20) DEFAULT 'permanent',
            session_date DATE
        );
    """)

    # Asegurar que class_teachers tenga la columna custom_teacher_name
    try:
        cur.execute("ALTER TABLE class_teachers ADD COLUMN custom_teacher_name VARCHAR(150);")
        conn.commit()
    except Exception:
        conn.rollback()

    # Tabla de Alumnos (asegurar que tenga la columna clase_id)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS students (
            id SERIAL PRIMARY KEY,
            first_name VARCHAR(100) NOT NULL,
            last_name VARCHAR(100) NOT NULL
        );
    """)
    try:
        cur.execute("ALTER TABLE students ADD COLUMN clase_id INTEGER REFERENCES classes(id) ON DELETE SET NULL;")
        conn.commit()
    except Exception:
        conn.rollback()
    
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

    # Asegurar que materials tenga la columna clase_id
    try:
        cur.execute("ALTER TABLE materials ADD COLUMN clase_id INTEGER REFERENCES classes(id) ON DELETE SET NULL;")
        conn.commit()
    except Exception:
        conn.rollback()

    # Tabla de Calendario de Clases Programadas
    cur.execute("""
        CREATE TABLE IF NOT EXISTS classes_schedule (
            id SERIAL PRIMARY KEY,
            month VARCHAR(50) NOT NULL,
            title VARCHAR(200) NOT NULL,
            meeting_link TEXT,
            scheduled_date DATE NOT NULL,
            class_id INTEGER REFERENCES classes(id) ON DELETE SET NULL
        );
    """)

    # Asegurar que classes_schedule tenga la columna class_id si la tabla ya existía
    try:
        cur.execute("ALTER TABLE classes_schedule ADD COLUMN class_id INTEGER REFERENCES classes(id) ON DELETE SET NULL;")
        conn.commit()
    except Exception:
        conn.rollback()

    # Tabla de Personal Manual (sin cuenta de usuario)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS staff (
            id SERIAL PRIMARY KEY,
            name VARCHAR(150) NOT NULL,
            position VARCHAR(100)
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
            clase_id = request.form.get('clase_id') or None
            cur.execute("INSERT INTO materials (name, total_stock, clase_id) VALUES (%s, %s, %s)", (mat_name, total_stock, clase_id))
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

    cur.execute("""
        SELECT m.id, m.name, m.total_stock, c.name AS clase_name
        FROM materials m
        LEFT JOIN classes c ON m.clase_id = c.id
        ORDER BY m.name ASC;
    """)
    materials_list = cur.fetchall()

    cur.execute("SELECT id, name FROM classes ORDER BY name ASC;")
    clases_list = cur.fetchall()
    
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
    
    return render_template('materials.html', materials=materials_list, requests=requests_list, clases=clases_list)

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
        class_id = request.form.get('class_id') or None
        
        cur.execute(
            "INSERT INTO classes_schedule (month, title, meeting_link, scheduled_date, class_id) VALUES (%s, %s, %s, %s, %s)",
            (month, title, meeting_link, scheduled_date, class_id)
        )
        conn.commit()
        flash('Clase programada con éxito.', 'success')
        
    cur.execute("""
        SELECT cs.id, cs.month, cs.title, cs.meeting_link, cs.scheduled_date, c.name AS class_name
        FROM classes_schedule cs
        LEFT JOIN classes c ON cs.class_id = c.id
        ORDER BY cs.scheduled_date ASC;
    """)
    classes = cur.fetchall()

    cur.execute("SELECT id, name FROM classes ORDER BY name ASC;")
    clases_list = cur.fetchall()
    
    cur.close()
    conn.close()
    
    return render_template('schedule.html', classes=classes, clases=clases_list)

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

                clase_val = request.form.get(f'clase_id_{s_id}')
                clase_id = int(clase_val) if clase_val and clase_val.isdigit() else None
                cur.execute("UPDATE students SET clase_id = %s WHERE id = %s", (clase_id, s_id))

            conn.commit()
            flash('Asistencia y asignación de clases guardadas correctamente.', 'success')

    cur.execute(
        "SELECT id, first_name, last_name, clase_id FROM students ORDER BY last_name ASC, first_name ASC;"
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
    
    # Imprimir en consola de Render para depurar
    print(f"DEBUG: Intentando actualizar estudiante ID {student_id} a clase ID {clase_id}")

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Convertir a entero o None
        val = int(clase_id) if (clase_id and clase_id.isdigit()) else None
        
        # Ejecutar actualización
        cur.execute("UPDATE students SET clase_id = %s WHERE id = %s", (val, student_id))
        
        # Verificar si realmente se actualizó algo
        if cur.rowcount == 0:
            print(f"ERROR: No se encontró al estudiante con ID {student_id}")
            flash('Error: No se encontró el alumno en la base de datos.', 'danger')
        else:
            conn.commit()
            flash('Clase actualizada con éxito.', 'success')
            
        cur.close()
        conn.close()
    except Exception as e:
        print(f"ERROR CRÍTICO: {str(e)}")
        flash(f'Error al guardar: {str(e)}', 'danger')

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
            class_id = request.form.get('class_id')
            raw_id = request.form.get('user_id', '')
            custom_teacher_name = request.form.get('custom_teacher_name', '').strip()

            u_id = None
            c_name = custom_teacher_name or None
            if raw_id.startswith('u_'):
                u_id = int(raw_id[2:])
                c_name = None
            elif raw_id.startswith('s_'):
                staff_id = int(raw_id[2:])
                cur.execute("SELECT name FROM staff WHERE id = %s", (staff_id,))
                staff_row = cur.fetchone()
                c_name = staff_row['name'] if staff_row else custom_teacher_name or None

            if class_id:
                cur.execute("""
                    INSERT INTO class_teachers (class_id, user_id, custom_teacher_name, assignment_type, session_date)
                    VALUES (%s, %s, %s, %s, %s)
                """, (class_id, u_id, c_name, 'permanent', None))
                conn.commit()
                flash('Maestro asignado correctamente.', 'success')

        elif 'update_teacher' in request.form:
            class_id = request.form.get('class_id')
            ct_id = request.form.get('ct_id')
            raw_id = request.form.get('user_id', '')
            custom_teacher_name = request.form.get('custom_teacher_name', '').strip()

            u_id = None
            c_name = custom_teacher_name or None
            if raw_id.startswith('u_'):
                u_id = int(raw_id[2:])
                c_name = None
            elif raw_id.startswith('s_'):
                staff_id = int(raw_id[2:])
                cur.execute("SELECT name FROM staff WHERE id = %s", (staff_id,))
                staff_row = cur.fetchone()
                c_name = staff_row['name'] if staff_row else custom_teacher_name or None

            if ct_id:
                cur.execute(
                    "UPDATE class_teachers SET user_id = %s, custom_teacher_name = %s WHERE id = %s",
                    (u_id, c_name, ct_id)
                )
            else:
                cur.execute(
                    "INSERT INTO class_teachers (class_id, user_id, custom_teacher_name) VALUES (%s, %s, %s)",
                    (class_id, u_id, c_name)
                )
            conn.commit()
            flash('Maestro asignado/actualizado correctamente.', 'success')

    cur.execute("SELECT * FROM classes ORDER BY name ASC;")
    clases = cur.fetchall()

    cur.execute("SELECT id, name, email FROM users ORDER BY name ASC;")
    users_list = cur.fetchall()

    cur.execute("SELECT id, name, position FROM staff ORDER BY name ASC;")
    staff_list = cur.fetchall()

    teachers = []
    for u in users_list:
        teachers.append({'id': f"u_{u['id']}", 'name': u['name'], 'source': 'user'})
    for s in staff_list:
        teachers.append({'id': f"s_{s['id']}", 'name': s['name'], 'source': 'staff'})

    cur.execute("""
        SELECT ct.class_id, ct.id as ct_id, ct.user_id, ct.custom_teacher_name
        FROM class_teachers ct
    """)
    raw_assignments = cur.fetchall()
    teacher_map = {}
    for ta in raw_assignments:
        if ta['class_id'] not in teacher_map:
            display_name = ta['custom_teacher_name']
            if not display_name and ta['user_id']:
                cur.execute("SELECT name FROM users WHERE id = %s", (ta['user_id'],))
                u_row = cur.fetchone()
                display_name = u_row['name'] if u_row else None
            teacher_map[ta['class_id']] = {**ta, 'teacher_name': display_name}

    cur.close()
    conn.close()
    return render_template('admin_classes.html', clases=clases, teachers=teachers, teacher_map=teacher_map)

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

        elif 'add_staff' in request.form:
            s_name = request.form['staff_name'].strip()
            s_position = request.form['staff_position'].strip()
            if s_name:
                cur.execute("INSERT INTO staff (name, position) VALUES (%s, %s)", (s_name, s_position or None))
                conn.commit()
                flash('Personal registrado manualmente con éxito.', 'success')

        elif 'delete_staff' in request.form:
            staff_id = request.form['staff_id']
            cur.execute("DELETE FROM staff WHERE id = %s", (staff_id,))
            conn.commit()
            flash('Personal eliminado correctamente.', 'success')

    cur.execute("SELECT id, name, email, role, position FROM users ORDER BY name ASC;")
    teachers = cur.fetchall()

    cur.execute("SELECT id, name, position FROM staff ORDER BY name ASC;")
    staff_list = cur.fetchall()

    cur.close()
    conn.close()
    return render_template('admin_teachers.html', teachers=teachers, staff=staff_list)

@app.route('/admin/attendance-stats', methods=['GET', 'POST'])
def attendance_stats():
    if 'user' not in session or session['user']['role'] != 'admin':
        flash('Acceso restringido.', 'danger')
        return redirect(url_for('dashboard'))

    conn = get_db_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        if 'add_strategy' in request.form:
            title = request.form['title']
            description = request.form['description']
            status = request.form['status']
            cur.execute("""
                INSERT INTO attendance_strategies (title, description, status) 
                VALUES (%s, %s, %s)
            """, (title, description, status))
            conn.commit()
            flash('Estrategia registrada con éxito.', 'success')

        elif 'update_strategy' in request.form:
            strat_id = request.form['strat_id']
            new_status = request.form['new_status']
            cur.execute("UPDATE attendance_strategies SET status = %s WHERE id = %s", (new_status, strat_id))
            conn.commit()
            flash('Estado de la estrategia actualizado.', 'success')

    # Estadísticas: asistencia por clase y fecha
    try:
        cur.execute("""
            SELECT c.name AS class_name,
                   a.class_date AS week_start,
                   COUNT(CASE WHEN a.present = true THEN 1 END) AS total_presentes,
                   COUNT(a.id) AS total_registrados,
                   ROUND(
                       CASE WHEN COUNT(a.id) > 0
                            THEN COUNT(CASE WHEN a.present = true THEN 1 END)::numeric / COUNT(a.id) * 100
                            ELSE 0
                       END, 1
                   ) AS porcentaje
            FROM attendance a
            JOIN students s ON a.student_id = s.id
            JOIN classes c ON s.clase_id = c.id
            GROUP BY c.name, a.class_date
            ORDER BY a.class_date DESC, c.name ASC;
        """)
        weekly_stats = cur.fetchall()
    except Exception:
        conn.rollback()
        weekly_stats = []

    # Maestros asignados por clase (usuarios + staff manual)
    cur.execute("""
        SELECT ct.class_id, ct.user_id, ct.custom_teacher_name, c.name as class_name
        FROM class_teachers ct
        JOIN classes c ON ct.class_id = c.id
    """)
    raw_ct = cur.fetchall()
    class_teachers_map = {}
    for row in raw_ct:
        cid = row['class_id']
        if cid not in class_teachers_map:
            class_teachers_map[cid] = {'class_name': row['class_name'], 'teachers': []}
        display = row['custom_teacher_name']
        if not display and row['user_id']:
            cur.execute("SELECT name FROM users WHERE id = %s", (row['user_id'],))
            u = cur.fetchone()
            display = u['name'] if u else 'Desconocido'
        class_teachers_map[cid]['teachers'].append(display or 'Sin nombre')

    # Todas las clases (para mostrar aunque no tengan maestro ni asistencia)
    cur.execute("SELECT id, name FROM classes ORDER BY name ASC;")
    all_classes = cur.fetchall()

    # Estrategias
    cur.execute("SELECT * FROM attendance_strategies ORDER BY id DESC;")
    strategies = cur.fetchall()

    cur.close()
    conn.close()
    return render_template(
        'attendance_stats.html',
        weekly_stats=weekly_stats,
        strategies=strategies,
        class_teachers_map=class_teachers_map,
        all_classes=all_classes,
    )

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

@app.route('/delete_material/<int:material_id>', methods=['POST'])
def delete_material(material_id):
    if 'user' not in session or session['user']['role'] != 'admin':
        flash('Acceso no autorizado.', 'danger')
        return redirect(url_for('materials'))

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM materials WHERE id = %s", (material_id,))
    conn.commit()
    cur.close()
    conn.close()

    flash('Material eliminado del inventario correctamente.', 'success')
    return redirect(url_for('materials'))

@app.route('/clase/<int:clase_id>/asistencia')
def ver_asistencia_clase(clase_id):
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT * FROM classes WHERE id = %s", (clase_id,))
    clase = cur.fetchone()
    if not clase:
        cur.close()
        conn.close()
        flash('Clase no encontrada.', 'danger')
        return redirect(url_for('dashboard'))

    # Estadísticas semanales de asistencia para esta clase
    try:
        cur.execute("""
            SELECT c.name AS class_name,
                   a.class_date AS week_start,
                   COUNT(CASE WHEN a.present = true THEN 1 END) AS total_presentes,
                   COUNT(a.id) AS total_registrados,
                   ROUND(
                       CASE WHEN COUNT(a.id) > 0
                            THEN COUNT(CASE WHEN a.present = true THEN 1 END)::numeric / COUNT(a.id) * 100
                            ELSE 0
                       END, 1
                   ) AS porcentaje
            FROM attendance a
            JOIN students s ON a.student_id = s.id
            JOIN classes c ON s.clase_id = c.id
            WHERE s.clase_id = %s
            GROUP BY c.name, a.class_date
            ORDER BY a.class_date DESC;
        """, (clase_id,))
        weekly_stats = cur.fetchall()
    except Exception:
        conn.rollback()
        weekly_stats = []

    # Maestros asignados a esta clase
    try:
        cur.execute("""
            SELECT ct.class_id, ct.user_id, ct.custom_teacher_name, c.name as class_name
            FROM class_teachers ct
            JOIN classes c ON ct.class_id = c.id
            WHERE ct.class_id = %s;
        """, (clase_id,))
        raw_ct = cur.fetchall()
        class_teachers_map = {}
        for row in raw_ct:
            cid = row['class_id']
            if cid not in class_teachers_map:
                class_teachers_map[cid] = {'class_name': row['class_name'], 'teachers': []}
            display = row['custom_teacher_name']
            if not display and row['user_id']:
                cur.execute("SELECT name FROM users WHERE id = %s", (row['user_id'],))
                u = cur.fetchone()
                display = u['name'] if u else 'Desconocido'
            class_teachers_map[cid]['teachers'].append(display or 'Sin nombre')
    except Exception:
        conn.rollback()
        class_teachers_map = {}

    # Solo esta clase para la sección de "sin maestro"
    cur.execute("SELECT id, name FROM classes WHERE id = %s ORDER BY name ASC;", (clase_id,))
    all_classes = cur.fetchall()

    # Estrategias
    try:
        cur.execute("SELECT * FROM attendance_strategies ORDER BY id DESC;")
        strategies = cur.fetchall()
    except Exception:
        conn.rollback()
        strategies = []

    cur.close()
    conn.close()
    return render_template(
        'attendance_stats.html',
        clase=clase,
        weekly_stats=weekly_stats,
        strategies=strategies,
        class_teachers_map=class_teachers_map,
        all_classes=all_classes,
    )

if __name__ == '__main__':
    app.run(debug=True, port=5000)