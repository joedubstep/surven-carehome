import hashlib


def new_message_id(domain: str, seed: str) -> str:
    h = hashlib.sha1(seed.encode()).hexdigest()[:20]
    return f"<{h}@{domain}>"


def reply_headers(original_message_id: str, original_subject: str) -> dict:
    subj = original_subject if original_subject.lower().startswith("re:") else f"Re: {original_subject}"
    return {
        "In-Reply-To": original_message_id,
        "References": original_message_id,
        "Subject": subj,
    }
