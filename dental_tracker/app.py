import csv
import io
from datetime import datetime, date, timedelta

from flask import Flask, render_template, request, redirect, url_for, flash, Response
from sqlalchemy import func

from database import db, Call, FollowUp

app = Flask(__name__)
app.config["SECRET_KEY"] = "dental-tracker-secret-2026"
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///dental_tracker.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)

LEAD_SOURCES = [
    "Google Business Profile",
    "Google Ads",
    "Organic SEO / Website",
    "Facebook Ads",
    "Instagram Ads",
    "Patient Referral",
    "Insurance Directory",
    "Direct / Walk-in",
    "Other",
]

CALL_OUTCOMES = ["booked", "not_booked", "missed", "voicemail", "transferred"]

REASONS = [
    "new_patient",
    "emergency",
    "existing_appt",
    "billing",
    "insurance",
    "cosmetic_consult",
    "other",
]

PROCEDURES = [
    "Cleaning / Checkup",
    "Emergency / Pain",
    "Implants",
    "Invisalign / Braces",
    "Whitening",
    "Extraction",
    "Root Canal",
    "Crowns / Veneers",
    "Pediatric Dentistry",
    "Other",
]


# ---------- Context processors ----------

@app.context_processor
def inject_globals():
    today = date.today()
    overdue_followups = Call.query.filter(
        Call.followup_required == True,
        Call.followup_status == "pending",
        Call.followup_date <= datetime.combine(today, datetime.max.time()),
    ).count()
    return dict(overdue_followups=overdue_followups, today=today)


# ---------- Dashboard ----------

@app.route("/")
def dashboard():
    today = date.today()
    month_start = today.replace(day=1)

    # Total calls this month
    total_calls = Call.query.filter(func.date(Call.created_at) >= month_start).count()

    # New leads (new patients) this month
    new_leads = Call.query.filter(
        Call.patient_type == "new",
        func.date(Call.created_at) >= month_start,
    ).count()

    # Conversions this month
    conversions = Call.query.filter(
        Call.converted_to_patient == True,
        func.date(Call.conversion_date) >= month_start,
    ).count()

    # Conversion rate
    conv_rate = round((conversions / new_leads * 100) if new_leads else 0, 1)

    # Emergency cases this month
    emergencies = Call.query.filter(
        Call.urgency == "emergency",
        func.date(Call.created_at) >= month_start,
    ).count()

    # Missed calls this month
    missed = Call.query.filter(
        Call.call_outcome == "missed",
        func.date(Call.created_at) >= month_start,
    ).count()

    # Follow-ups due today or overdue
    followups_due = Call.query.filter(
        Call.followup_required == True,
        Call.followup_status == "pending",
        Call.followup_date <= datetime.combine(today, datetime.max.time()),
    ).all()

    # Leads by source (this month)
    source_data = (
        db.session.query(Call.lead_source, func.count(Call.id))
        .filter(func.date(Call.created_at) >= month_start, Call.patient_type == "new")
        .group_by(Call.lead_source)
        .all()
    )

    # Conversion by source
    conv_by_source = (
        db.session.query(Call.lead_source, func.count(Call.id))
        .filter(
            Call.converted_to_patient == True,
            func.date(Call.conversion_date) >= month_start,
        )
        .group_by(Call.lead_source)
        .all()
    )
    conv_source_map = {s: c for s, c in conv_by_source}

    source_stats = []
    for source, count in source_data:
        conv_count = conv_source_map.get(source, 0)
        source_stats.append({
            "source": source,
            "leads": count,
            "conversions": conv_count,
            "rate": round((conv_count / count * 100) if count else 0, 1),
        })
    source_stats.sort(key=lambda x: x["leads"], reverse=True)

    # Outcome breakdown
    outcome_data = (
        db.session.query(Call.call_outcome, func.count(Call.id))
        .filter(func.date(Call.created_at) >= month_start)
        .group_by(Call.call_outcome)
        .all()
    )

    # Recent calls
    recent_calls = Call.query.order_by(Call.created_at.desc()).limit(8).all()

    # Daily calls last 14 days for sparkline
    daily_labels = []
    daily_counts = []
    for i in range(13, -1, -1):
        d = today - timedelta(days=i)
        cnt = Call.query.filter(func.date(Call.created_at) == d).count()
        daily_labels.append(d.strftime("%b %d"))
        daily_counts.append(cnt)

    answer_rate = round(((total_calls - missed) / total_calls * 100) if total_calls else 0, 1)

    return render_template(
        "dashboard.html",
        total_calls=total_calls,
        new_leads=new_leads,
        conversions=conversions,
        conv_rate=conv_rate,
        emergencies=emergencies,
        missed=missed,
        answer_rate=answer_rate,
        followups_due=followups_due,
        source_stats=source_stats,
        outcome_data=outcome_data,
        recent_calls=recent_calls,
        daily_labels=daily_labels,
        daily_counts=daily_counts,
        month_name=today.strftime("%B %Y"),
    )


# ---------- Calls ----------

@app.route("/calls")
def calls_list():
    page = request.args.get("page", 1, type=int)
    source_filter = request.args.get("source", "")
    outcome_filter = request.args.get("outcome", "")
    urgency_filter = request.args.get("urgency", "")
    search = request.args.get("q", "")

    query = Call.query

    if source_filter:
        query = query.filter(Call.lead_source == source_filter)
    if outcome_filter:
        query = query.filter(Call.call_outcome == outcome_filter)
    if urgency_filter:
        query = query.filter(Call.urgency == urgency_filter)
    if search:
        query = query.filter(
            (Call.caller_name.ilike(f"%{search}%")) | (Call.caller_phone.ilike(f"%{search}%"))
        )

    calls = query.order_by(Call.created_at.desc()).paginate(page=page, per_page=20)

    return render_template(
        "calls/list.html",
        calls=calls,
        lead_sources=LEAD_SOURCES,
        call_outcomes=CALL_OUTCOMES,
        source_filter=source_filter,
        outcome_filter=outcome_filter,
        urgency_filter=urgency_filter,
        search=search,
    )


@app.route("/calls/add", methods=["GET", "POST"])
def calls_add():
    if request.method == "POST":
        f = request.form

        # Parse appointment datetime
        appt_dt = None
        if f.get("appointment_date") and f.get("appointment_time"):
            try:
                appt_dt = datetime.strptime(
                    f["appointment_date"] + " " + f["appointment_time"], "%Y-%m-%d %H:%M"
                )
            except ValueError:
                pass

        # Parse followup date
        fu_date = None
        if f.get("followup_date"):
            try:
                fu_date = datetime.strptime(f["followup_date"], "%Y-%m-%d")
            except ValueError:
                pass

        pain = f.get("pain_level")
        call = Call(
            caller_name=f["caller_name"].strip(),
            caller_phone=f["caller_phone"].strip(),
            patient_type=f.get("patient_type", "new"),
            lead_source=f["lead_source"],
            campaign_name=f.get("campaign_name", "").strip() or None,
            call_outcome=f["call_outcome"],
            reason=f.get("reason"),
            procedure_interest=f.get("procedure_interest"),
            urgency=f.get("urgency", "routine"),
            pain_level=int(pain) if pain and pain.isdigit() else None,
            has_swelling=bool(f.get("has_swelling")),
            has_trauma=bool(f.get("has_trauma")),
            appointment_datetime=appt_dt,
            insurance_collected=bool(f.get("insurance_collected")),
            staff_name=f.get("staff_name", "").strip() or None,
            notes=f.get("notes", "").strip() or None,
            followup_required=bool(f.get("followup_required")),
            followup_date=fu_date,
            followup_status="pending" if f.get("followup_required") else None,
        )
        db.session.add(call)
        db.session.commit()
        flash(f"Call from {call.caller_name} logged successfully!", "success")
        return redirect(url_for("call_detail", call_id=call.id))

    return render_template(
        "calls/add.html",
        lead_sources=LEAD_SOURCES,
        call_outcomes=CALL_OUTCOMES,
        reasons=REASONS,
        procedures=PROCEDURES,
        today_iso=date.today().isoformat(),
    )


@app.route("/calls/<int:call_id>")
def call_detail(call_id):
    call = Call.query.get_or_404(call_id)
    return render_template("calls/detail.html", call=call, procedures=PROCEDURES)


@app.route("/calls/<int:call_id>/convert", methods=["POST"])
def call_convert(call_id):
    call = Call.query.get_or_404(call_id)
    call.converted_to_patient = True
    call.conversion_date = datetime.utcnow()
    call.followup_status = "completed"
    db.session.commit()
    flash(f"{call.caller_name} has been marked as a converted patient!", "success")
    return redirect(url_for("call_detail", call_id=call_id))


@app.route("/calls/<int:call_id>/followup", methods=["POST"])
def add_followup(call_id):
    call = Call.query.get_or_404(call_id)
    f = request.form
    outcome = f.get("outcome")
    fu = FollowUp(
        call_id=call_id,
        outcome=outcome,
        notes=f.get("notes", "").strip() or None,
        staff_name=f.get("staff_name", "").strip() or None,
    )
    db.session.add(fu)

    if outcome in ("booked", "lost"):
        call.followup_status = "completed" if outcome == "booked" else "lost"
        if outcome == "booked":
            call.converted_to_patient = True
            call.conversion_date = datetime.utcnow()

    db.session.commit()
    flash("Follow-up attempt recorded.", "success")
    return redirect(url_for("call_detail", call_id=call_id))


# ---------- Follow-ups ----------

@app.route("/followups")
def followups():
    today = date.today()
    overdue = Call.query.filter(
        Call.followup_required == True,
        Call.followup_status == "pending",
        Call.followup_date < datetime.combine(today, datetime.min.time()),
    ).order_by(Call.followup_date.asc()).all()

    due_today = Call.query.filter(
        Call.followup_required == True,
        Call.followup_status == "pending",
        func.date(Call.followup_date) == today,
    ).order_by(Call.followup_date.asc()).all()

    upcoming = Call.query.filter(
        Call.followup_required == True,
        Call.followup_status == "pending",
        Call.followup_date > datetime.combine(today, datetime.max.time()),
    ).order_by(Call.followup_date.asc()).limit(20).all()

    return render_template(
        "followups.html",
        overdue=overdue,
        due_today=due_today,
        upcoming=upcoming,
    )


# ---------- Reports ----------

@app.route("/reports")
def reports():
    date_from = request.args.get("from", (date.today().replace(day=1)).isoformat())
    date_to = request.args.get("to", date.today().isoformat())

    try:
        dt_from = datetime.strptime(date_from, "%Y-%m-%d")
        dt_to = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
    except ValueError:
        dt_from = datetime.utcnow().replace(day=1)
        dt_to = datetime.utcnow() + timedelta(days=1)

    base = Call.query.filter(Call.created_at >= dt_from, Call.created_at < dt_to)

    total = base.count()
    new_leads = base.filter(Call.patient_type == "new").count()
    converted = base.filter(Call.converted_to_patient == True).count()
    emergencies = base.filter(Call.urgency == "emergency").count()
    missed = base.filter(Call.call_outcome == "missed").count()

    # Source breakdown
    source_rows = (
        db.session.query(Call.lead_source, func.count(Call.id))
        .filter(Call.created_at >= dt_from, Call.created_at < dt_to)
        .group_by(Call.lead_source)
        .all()
    )

    # Conversion by source
    conv_rows = (
        db.session.query(Call.lead_source, func.count(Call.id))
        .filter(
            Call.created_at >= dt_from,
            Call.created_at < dt_to,
            Call.converted_to_patient == True,
        )
        .group_by(Call.lead_source)
        .all()
    )
    conv_map = {s: c for s, c in conv_rows}

    source_report = []
    for src, cnt in sorted(source_rows, key=lambda x: x[1], reverse=True):
        c = conv_map.get(src, 0)
        source_report.append({
            "source": src,
            "calls": cnt,
            "conversions": c,
            "rate": round((c / cnt * 100) if cnt else 0, 1),
        })

    # Daily trend
    df = dt_from.date()
    dt_end = (dt_to - timedelta(days=1)).date()
    trend_labels = []
    trend_calls = []
    trend_conversions = []
    d = df
    while d <= dt_end:
        trend_labels.append(d.strftime("%b %d"))
        trend_calls.append(
            Call.query.filter(func.date(Call.created_at) == d).count()
        )
        trend_conversions.append(
            Call.query.filter(
                func.date(Call.created_at) == d,
                Call.converted_to_patient == True,
            ).count()
        )
        d += timedelta(days=1)

    return render_template(
        "reports.html",
        date_from=date_from,
        date_to=date_to,
        total=total,
        new_leads=new_leads,
        converted=converted,
        emergencies=emergencies,
        missed=missed,
        conv_rate=round((converted / new_leads * 100) if new_leads else 0, 1),
        answer_rate=round(((total - missed) / total * 100) if total else 0, 1),
        source_report=source_report,
        trend_labels=trend_labels,
        trend_calls=trend_calls,
        trend_conversions=trend_conversions,
    )


# ---------- CSV Export ----------

@app.route("/export/calls.csv")
def export_csv():
    calls = Call.query.order_by(Call.created_at.desc()).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "ID", "Date", "Caller Name", "Phone", "Patient Type",
        "Lead Source", "Outcome", "Reason", "Procedure", "Urgency",
        "Pain Level", "Swelling", "Trauma", "Appointment",
        "Insurance Collected", "Staff", "Converted", "Conversion Date",
        "Follow-up Required", "Follow-up Status", "Follow-up Date", "Notes",
    ])
    for c in calls:
        writer.writerow([
            c.id, c.created_at.strftime("%Y-%m-%d %H:%M"),
            c.caller_name, c.caller_phone, c.patient_type,
            c.lead_source, c.call_outcome, c.reason, c.procedure_interest, c.urgency,
            c.pain_level or "", "Yes" if c.has_swelling else "No",
            "Yes" if c.has_trauma else "No",
            c.appointment_datetime.strftime("%Y-%m-%d %H:%M") if c.appointment_datetime else "",
            "Yes" if c.insurance_collected else "No",
            c.staff_name or "", "Yes" if c.converted_to_patient else "No",
            c.conversion_date.strftime("%Y-%m-%d") if c.conversion_date else "",
            "Yes" if c.followup_required else "No",
            c.followup_status or "",
            c.followup_date.strftime("%Y-%m-%d") if c.followup_date else "",
            c.notes or "",
        ])
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=dental_calls.csv"},
    )


# ---------- Init & Run ----------

with app.app_context():
    db.create_all()

if __name__ == "__main__":
    app.run(debug=True, port=5050)
