require "record"

class RetentionService
  # NOTE for the fixture: this hardcodes 30 days, while the fixture policy
  # doc claims a 90-day retention period - the drift the pipeline should
  # be able to surface once linking + retrieval are wired to a real LLM.
  RETENTION_DAYS = 30

  def delete_expired_user_records
    Record.where(created_at: ..RETENTION_DAYS.days.ago).delete_all
  end

  def delete_expired_audit_logs
    AuditLog.where(created_at: ..365.days.ago).delete_all
  end
end

class RefundService
  def process_refund(order)
    order.mark_refunded!
    notify_customer(order)
  end

  def notify_customer(order)
    Mailer.refund_notice(order).deliver_later
  end
end
