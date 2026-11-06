# Gemini General Intelligence Enhancement (GGIE)

Bộ cấu hình dùng chung cho **Gemini Web và Gemini CLI**, ưu tiên mục đích sử dụng tổng quát, mức tự chủ balanced.

> GGIE không thay đổi trọng số hoặc năng lực nền của Gemini. Nó cải thiện quy trình làm việc: hiểu yêu cầu, chọn mức nỗ lực phù hợp, kiểm chứng thông tin, quản lý bối cảnh và kiểm soát hành động.

## Cấu trúc

- `web/SKILL.md`: Skill dùng trên Gemini Apps Web.
- `cli/GEMINI.md`: chỉ dẫn nền cho Gemini CLI.
- `cli/skills/ggi-core/SKILL.md`: Skill dùng trên Gemini CLI.
- `templates/TASK_BRIEF.md`: mẫu giao việc.
- `templates/PROJECT_CONTEXT.md`: mẫu lưu bối cảnh dự án.
- `templates/EVIDENCE_LEDGER.md`: sổ bằng chứng và kiểm thử.
- `templates/EVAL_SUITE.md`: bộ prompt đánh giá trước/sau.

## Phân biệt môi trường

### Gemini Web
Dùng Skills (nếu tài khoản có), prompt và tệp tham chiếu. Không giả định Skill có thể chạy shell, truy cập tùy ý ổ đĩa, chạy test hoặc sửa Git workspace. Khi cần thao tác máy tính, chuyển sang CLI hoặc công cụ tích hợp có quyền tương ứng.

### Gemini CLI
Có thể đọc/ghi tệp, chạy lệnh và dùng MCP theo phiên bản, cấu hình, folder trust và quyền được cấp. Không bật YOLO; không vượt qua approval; không tự ý xóa, deploy, publish hoặc thay đổi quyền.

## Cài trên Gemini Web

1. Mở Gemini Apps và tìm mục **Customize → Skills** (nếu tài khoản/phiên bản có).
2. Tạo Skill bằng chức năng upload/import, chọn thư mục `web` hoặc file `web/SKILL.md`.
3. Kiểm tra nội dung, lưu và bật Skill.
4. Nếu không có Skills, dùng nội dung `web/SKILL.md` làm chỉ dẫn đầu phiên hoặc tải file làm tài liệu tham chiếu.

Tên mục và khả năng import có thể thay đổi theo rollout/tài khoản. Không coi Skills là quyền truy cập máy tính.

## Cài trên Gemini CLI (Windows PowerShell)

Cài CLI theo tài liệu chính thức của Google, sau đó từ root repo chạy:

```powershell
$src = (Resolve-Path ".\tools\gemini-general-intelligence-enhancement\cli").Path
$dst = Join-Path $HOME ".gemini"
New-Item -ItemType Directory -Force -Path $dst | Out-Null

# Không ghi đè file global hiện hữu.
if (-not (Test-Path (Join-Path $dst "GEMINI.md"))) {
  Copy-Item (Join-Path $src "GEMINI.md") (Join-Path $dst "GEMINI.md")
} else {
  Write-Host "Đã có ~/.gemini/GEMINI.md. Hãy hợp nhất thủ công."
}

$skillDst = Join-Path $dst "skills\ggi-core"
New-Item -ItemType Directory -Force -Path $skillDst | Out-Null
Copy-Item (Join-Path $src "skills\ggi-core\SKILL.md") (Join-Path $skillDst "SKILL.md") -Force
```

Mở Gemini CLI và kiểm tra bằng các lệnh slash được phiên bản hiện tại hỗ trợ (ví dụ `/memory show`, `/skills list`; nếu khác, dùng `/help`). Không bật `--yolo`. Không thay thế `settings.json` hiện có bằng cấu hình mẫu chưa đối chiếu phiên bản.

## Các chế độ làm việc

- QUICK: việc đơn giản, trả lời thẳng.
- RESEARCH: dữ kiện mới, cần nguồn.
- REASONING: chẩn đoán, so sánh, vấn đề phức tạp.
- CREATE: tạo sản phẩm/draft hoàn chỉnh.
- EXECUTE: thao tác trên tệp/công cụ.
- DECIDE: lựa chọn theo tiêu chí.
- RECOVER: phân tích lỗi, sửa nhỏ, chạy lại kiểm thử.

Chọn chế độ nhẹ nhất đáp ứng yêu cầu. Không áp quy trình nặng cho câu hỏi đơn giản.

## Mức tự chủ balanced

Tự làm các bước thường lệ, ít rủi ro, có thể đảo ngược trong phạm vi yêu cầu. Hỏi xác nhận trước khi xóa/ghi đè, refactor diện rộng, deploy, publish, gửi dữ liệu ra ngoài, mua hàng, thay đổi quyền/credential hoặc tác động production.

## Kiểm chứng

Báo rõ DONE / VERIFIED / NOT RUN / BLOCKED / UNKNOWN. Không gọi build, parse, unit test hoặc một bước thành công là bằng chứng cho toàn bộ end-to-end. Không có log thì không nói test PASS.

## Đánh giá

Chạy `templates/EVAL_SUITE.md` trước và sau khi bật GGIE; giữ nguyên prompt và lưu đầu ra. Chỉ kết luận có cải thiện khi có so sánh thực tế. Bộ prompt nhỏ không chứng minh nâng cấp trí tuệ tổng quát.
