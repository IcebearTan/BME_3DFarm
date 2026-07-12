"""邮件通知（Flask-Mail）。SMTP 未配时跳过 + 日志，绝不阻塞业务。"""
from flask import current_app
from flask_mail import Message

from exts import db, mail
from models import UserModel


def smtp_configured():
    return bool(
        current_app.config.get("MAIL_SERVER")
        and current_app.config.get("MAIL_USERNAME")
    )


def send_completion_notice(order):
    """打印完成 → 发客户邮件。SMTP 未配 / 无邮箱 → 跳过。返回是否发出。"""
    if not smtp_configured():
        current_app.logger.info(
            "[mail] SMTP 未配，跳过完成通知 order=%s", order.id
        )
        return False
    user = db.session.get(UserModel, order.user_id) if order.user_id else None
    if not user or not user.email:
        return False
    try:
        msg = Message(
            subject=f"打印完成 · 订单 {order.order_no}",
            recipients=[user.email],
            body=(
                f"您的订单 {order.order_no} 已打印完成，实扣 "
                f"{order.actual_credit} credit。请前往 3D 农场查看详情。"
            ),
        )
        mail.send(msg)
        return True
    except Exception as e:  # 邮件失败不影响主流程
        current_app.logger.warning("[mail] 发送失败 order=%s: %s", order.id, e)
        return False
