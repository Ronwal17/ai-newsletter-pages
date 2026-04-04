from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()


class Call(db.Model):
    __tablename__ = "calls"

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # Caller info
    caller_name = db.Column(db.String(120), nullable=False)
    caller_phone = db.Column(db.String(20), nullable=False)
    patient_type = db.Column(db.String(20), default="new")  # new | existing

    # Lead source
    lead_source = db.Column(db.String(50), nullable=False)
    campaign_name = db.Column(db.String(100))

    # Call details
    call_outcome = db.Column(db.String(30), nullable=False)  # booked | not_booked | missed | voicemail
    reason = db.Column(db.String(50))  # new_patient | emergency | existing_appt | billing | cosmetic | other
    procedure_interest = db.Column(db.String(50))

    # Urgency / Emergency triage
    urgency = db.Column(db.String(20), default="routine")  # routine | urgent | emergency
    pain_level = db.Column(db.Integer)   # 1-10
    has_swelling = db.Column(db.Boolean, default=False)
    has_trauma = db.Column(db.Boolean, default=False)

    # Appointment
    appointment_datetime = db.Column(db.DateTime)
    insurance_collected = db.Column(db.Boolean, default=False)
    staff_name = db.Column(db.String(80))
    notes = db.Column(db.Text)

    # Conversion tracking
    converted_to_patient = db.Column(db.Boolean, default=False)
    conversion_date = db.Column(db.DateTime)

    # Follow-up
    followup_required = db.Column(db.Boolean, default=False)
    followup_date = db.Column(db.DateTime)
    followup_status = db.Column(db.String(30), default="pending")  # pending | completed | lost

    followups = db.relationship("FollowUp", backref="call", lazy=True, cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Call {self.id} - {self.caller_name}>"


class FollowUp(db.Model):
    __tablename__ = "followups"

    id = db.Column(db.Integer, primary_key=True)
    call_id = db.Column(db.Integer, db.ForeignKey("calls.id"), nullable=False)
    attempted_at = db.Column(db.DateTime, default=datetime.utcnow)
    outcome = db.Column(db.String(40))  # reached | no_answer | voicemail | booked | lost
    notes = db.Column(db.Text)
    staff_name = db.Column(db.String(80))
