# Prompt 8: which datasets, and letting users upload their own data

- Tool: Claude (sent in the project chat and forwarded to the build thread unchanged)
- Sent: 2026-11-12 03:30 UTC
- Content: verbatim.

---

```
What are the datasets that you have used and how to make sure the user has chance to upload the data and check for the results and all
```

## Follow-up choice (2026-11-12 03:31 UTC)

The agent asked "Add a CSV upload page so users can test their own feedback?" with two options. The
candidate picked **"Yes, add CSV upload"**. This produced the commit that adds `POST /api/feedback/upload`,
the template endpoint, the Upload Feedback dashboard page and `tests/test_upload.py`.
