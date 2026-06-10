import os
from flask import Flask, jsonify, request
from flask_cors import CORS
import psycopg2
import psycopg2.extras
from decimal import Decimal

app = Flask(__name__)
CORS(app)

DB_CONFIG = {
    "host":     os.environ.get("DB_HOST",     "pg-160f1d19-tooshadrack.i.aivencloud.com"),
    "port":     os.environ.get("DB_PORT",     "28148"),
    "dbname":   os.environ.get("DB_NAME",     "postgres"),
    "user":     os.environ.get("DB_USER",     "avnadmin"),
    "password": os.environ.get("DB_PASSWORD", "AVNS_0GkA_sQBaqoz0AWvXhD"),
    "sslmode":  os.environ.get("DB_SSLMODE",  "require")
}

def get_connection():
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        return conn
    except Exception as e:
        print(f"❌ Database connection error: {e}")
        return None

# Helper function to calculate RCI and condition
def calculate_rci_condition(density):
    """Calculate RCI and condition based on density (potholes/km)"""
    if density == 0:
        return 1.00, "Perfect", "#228B22"
    elif density <= 2.0:
        rci = round(1.0 - (density / 10.0), 2)
        return rci, "Good", "#32CD32"
    elif density <= 6.0:
        rci = round(1.0 - (density / 10.0), 2)
        return rci, "Average", "#FFA500"
    else:  # density > 6.0 (including >= 10)
        return 0.00, "Poor", "#EF4444"

@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "message": "Road Vision API is running"})

@app.route("/api/roads")
def get_roads():
    conn = get_connection()
    if not conn:
        return jsonify({"success": False, "error": "Database connection failed"}), 500
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        # Simplified query without ROUND in SQL - we'll calculate in Python
        cur.execute("""
            WITH latest_inspections AS (
                SELECT road_gid, MAX(timestamp::date) AS latest_date
                FROM parc.roadpothole GROUP BY road_gid
            ),
            current_counts AS (
                SELECT p.road_gid, COUNT(p.id) AS pothole_count
                FROM parc.roadpothole p
                JOIN latest_inspections li ON p.road_gid = li.road_gid
                    AND p.timestamp::date = li.latest_date
                GROUP BY p.road_gid
            )
            SELECT
                r.gid, r.roadname, r.roadtype, r.roadagency, r.roadcode,
                r.roadclass, r.county,
                r.length_km,
                r.length_m,
                COALESCE(cc.pothole_count, 0) AS pothole_count,
                CASE 
                    WHEN r.length_km > 0 
                    THEN (COALESCE(cc.pothole_count, 0)::float / r.length_km)
                    ELSE 0 
                END AS density_per_km,
                ST_AsGeoJSON(r.geom)::json AS geometry
            FROM parc.jujatarmacrds r
            LEFT JOIN current_counts cc ON r.gid = cc.road_gid
            WHERE r.geom IS NOT NULL
            ORDER BY r.roadname ASC
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()

        result_roads = []
        for row in rows:
            road = dict(row)
            
            # Get density as float
            density_raw = road.get('density_per_km', 0)
            if density_raw is None:
                density = 0.0
            else:
                density = float(density_raw)
            
            # Round density to 2 decimal places
            road['density_per_km'] = round(density, 2)
            
            # Calculate RCI and condition using Python function
            rci, condition, color = calculate_rci_condition(density)
            road['rci_value'] = rci
            road['condition'] = condition
            road['road_color'] = color
            
            # Round length_km if it exists
            if road.get('length_km'):
                road['length_km'] = round(float(road['length_km']), 2)
            
            # Debug output for density >= 10
            if density >= 10.0:
                print(f"✅ Road {road['roadname']}: Density={density}, RCI={rci}, Condition={condition}")
            
            result_roads.append(road)

        return jsonify({"success": True, "roads": result_roads})
    except Exception as e:
        print(f"❌ Error in get_roads: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/stats")
def get_stats():
    conn = get_connection()
    if not conn:
        return jsonify({"success": False, "error": "Database connection failed"}), 500
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT COUNT(*) as count FROM parc.jujatarmacrds WHERE geom IS NOT NULL")
        total_roads = cur.fetchone()['count']
        
        cur.execute("""
            WITH latest_inspections AS (
                SELECT road_gid, MAX(timestamp::date) AS latest_date
                FROM parc.roadpothole GROUP BY road_gid
            ),
            current_counts AS (
                SELECT p.road_gid, COUNT(p.id) AS pothole_count
                FROM parc.roadpothole p
                JOIN latest_inspections li ON p.road_gid = li.road_gid
                    AND p.timestamp::date = li.latest_date
                GROUP BY p.road_gid
            )
            SELECT
                SUM(COALESCE(cc.pothole_count, 0)) as total_potholes,
                COUNT(CASE WHEN (COALESCE(cc.pothole_count, 0)::float / NULLIF(r.length_km, 0)) > 6.0 THEN 1 END) as poor_roads,
                COUNT(CASE WHEN (COALESCE(cc.pothole_count, 0)::float / NULLIF(r.length_km, 0)) > 2.0
                           AND (COALESCE(cc.pothole_count, 0)::float / NULLIF(r.length_km, 0)) <= 6.0 THEN 1 END) as average_roads,
                COUNT(CASE WHEN COALESCE(cc.pothole_count, 0) > 0
                           AND (COALESCE(cc.pothole_count, 0)::float / NULLIF(r.length_km, 0)) <= 2.0 THEN 1 END) as good_roads,
                COUNT(CASE WHEN COALESCE(cc.pothole_count, 0) = 0 THEN 1 END) as perfect_roads
            FROM parc.jujatarmacrds r
            LEFT JOIN current_counts cc ON r.gid = cc.road_gid
            WHERE r.length_km > 0
        """)
        stats = cur.fetchone()
        cur.close()
        conn.close()

        return jsonify({
            "success": True,
            "stats": {
                "total_roads": total_roads or 0,
                "total_potholes": int(stats['total_potholes']) if stats['total_potholes'] else 0,
                "poor_roads": int(stats['poor_roads']) if stats['poor_roads'] else 0,
                "average_roads": int(stats['average_roads']) if stats['average_roads'] else 0,
                "good_roads": int(stats['good_roads']) if stats['good_roads'] else 0,
                "perfect_roads": int(stats['perfect_roads']) if stats['perfect_roads'] else 0
            }
        })
    except Exception as e:
        print(f"❌ Error in get_stats: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/juja_boundary")
def get_juja_boundary():
    conn = get_connection()
    if not conn:
        return jsonify({"success": False, "error": "Database connection failed"}), 500
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT ST_AsGeoJSON(ST_Collect(geom))::json as boundary_geojson FROM parc.juja WHERE geom IS NOT NULL")
        row = cur.fetchone()
        cur.close()
        conn.close()
        
        if row and row['boundary_geojson']:
            return jsonify({"success": True, "boundary": row['boundary_geojson']})
        return jsonify({"success": False, "error": "No boundary data found"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/timeline")
def get_timeline():
    try:
        road_gid = request.args.get('road_gid')
        date_from = request.args.get('from', '2020-01-01')
        date_to = request.args.get('to', '2099-12-31')
        
        if not road_gid:
            return jsonify({"success": False, "error": "road_gid is required"}), 400
        
        conn = get_connection()
        if not conn:
            return jsonify({"success": False, "error": "Database connection failed"}), 500
        
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT 
                p.timestamp::date AS period, 
                COUNT(p.id) AS pothole_count,
                r.length_km
            FROM parc.roadpothole p
            JOIN parc.jujatarmacrds r ON p.road_gid = r.gid
            WHERE p.road_gid = %s AND p.timestamp >= %s AND p.timestamp <= %s
            GROUP BY p.timestamp::date, r.length_km
            ORDER BY period ASC
        """, (road_gid, date_from, date_to))
        timeline_points = cur.fetchall()
        
        # Calculate density, RCI, and condition for each point
        for point in timeline_points:
            potholes = int(point['pothole_count'])
            length_km = float(point['length_km']) if point['length_km'] else 1.0
            density = potholes / length_km if length_km > 0 else 0
            point['density_per_km'] = round(density, 2)
            
            rci, condition, _ = calculate_rci_condition(density)
            point['rci_value'] = rci
            point['condition'] = condition
        
        cur.execute("""
            SELECT COUNT(p.id) AS current_potholes FROM parc.roadpothole p
            WHERE p.road_gid=%s AND p.timestamp>=%s AND p.timestamp<=%s
              AND p.timestamp::date = (
                  SELECT MAX(timestamp::date) FROM parc.roadpothole
                  WHERE road_gid=%s AND timestamp>=%s AND timestamp<=%s)
        """, (road_gid, date_from, date_to, road_gid, date_from, date_to))
        cr = cur.fetchone()
        cur.close()
        conn.close()
        
        return jsonify({
            "success": True,
            "points": [dict(r) for r in timeline_points],
            "current_potholes": int(cr['current_potholes']) if cr and cr['current_potholes'] else 0
        })
    except Exception as e:
        print(f"❌ Error in get_timeline: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/analysis")
def get_analysis():
    conn = get_connection()
    if not conn:
        return jsonify({"success": False, "error": "Database connection failed"}), 500
    try:
        road_gid = request.args.get('road_gid')
        date_from = request.args.get('from', '2020-01-01')
        date_to = request.args.get('to', '2099-12-31')
        
        if not road_gid:
            return jsonify({"success": False, "error": "road_gid is required"}), 400

        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute("""
            SELECT gid, roadname, roadtype, roadagency, roadcode, roadclass, length_km
            FROM parc.jujatarmacrds WHERE gid = %s
        """, (road_gid,))
        road_info = cur.fetchone()
        if not road_info:
            return jsonify({"success": False, "error": "Road not found"}), 404

        cur.execute("""
            SELECT
                DATE_TRUNC('month', p.timestamp) AS month,
                COUNT(p.id) AS pothole_count,
                r.length_km
            FROM parc.roadpothole p
            JOIN parc.jujatarmacrds r ON p.road_gid = r.gid
            WHERE p.road_gid = %s
                AND p.timestamp >= %s
                AND p.timestamp <= %s
            GROUP BY DATE_TRUNC('month', p.timestamp), r.length_km
            ORDER BY month ASC
        """, (road_gid, date_from, date_to))
        rows = cur.fetchall()
        cur.close()
        conn.close()

        print(f"\n{'='*60}")
        print(f"🔍 ANALYSIS FOR ROAD: {road_info['roadname']}")
        print(f"{'='*60}")
        
        condition_counts = {'Perfect': 0, 'Good': 0, 'Average': 0, 'Poor': 0}
        data = []
        
        for row in rows:
            month_str = row['month'].strftime('%b %Y')
            potholes = int(row['pothole_count'])
            length_km = float(row['length_km']) if row['length_km'] else 1.0
            density = potholes / length_km if length_km > 0 else 0
            
            rci, condition, _ = calculate_rci_condition(density)
            
            if density >= 10.0:
                print(f"✅ {month_str}: Density={density:.2f} → RCI={rci:.2f}, Condition={condition}")
            else:
                print(f"   {month_str}: Density={density:.2f} → RCI={rci:.2f}, Condition={condition}")
            
            condition_counts[condition] = condition_counts.get(condition, 0) + 1
            
            data.append({
                'month': row['month'].strftime('%Y-%m'),
                'month_display': month_str,
                'potholes': potholes,
                'density': round(density, 2),
                'rci': rci,
                'condition': condition
            })

        dominant_condition = max(condition_counts, key=condition_counts.get) if data else 'N/A'
        
        print(f"\n📊 SUMMARY: {condition_counts}")
        print(f"🏆 Dominant Condition: {dominant_condition}")
        print(f"{'='*60}\n")

        return jsonify({
            "success": True,
            "road_info": {
                "name": road_info['roadname'],
                "type": road_info['roadtype'] or '',
                "agency": road_info['roadagency'] or '',
                "code": road_info['roadcode'] or '',
                "class": road_info['roadclass'] or '',
                "length_km": float(road_info['length_km']) if road_info['length_km'] else 0
            },
            "data": data,
            "months_tracked": len(data),
            "dominant_condition": dominant_condition,
            "condition_counts": condition_counts
        })
    except Exception as e:
        print(f"❌ Error in get_analysis: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Starting Road Vision API on port {port}...")
    app.run(host="0.0.0.0", port=port)
