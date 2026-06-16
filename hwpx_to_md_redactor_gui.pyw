"""HWPX → MD 변환 + 개인정보 제거 — GUI Launcher"""
import subprocess
import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path


def main():
    root = tk.Tk()
    root.title("HWPX → MD 개인정보 제거기")
    root.geometry("440x180")
    root.attributes("-topmost", True)
    root.resizable(False, False)

    root.update_idletasks()
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    x = (sw - 440) // 2
    y = (sh - 180) // 2
    root.geometry(f"440x180+{x}+{y}")

    title = tk.Label(
        root,
        text="HWPX → MD 개인정보 제거기",
        font=("맑은 고딕", 14, "bold"),
    )
    title.pack(pady=(15, 5))

    desc = tk.Label(
        root,
        text="HWPX를 MD로 변환한 뒤 개인정보를 자동 제거합니다",
        font=("맑은 고딕", 9),
        fg="gray",
    )
    desc.pack(pady=(0, 10))

    def select_and_run():
        root.attributes("-topmost", False)
        files = filedialog.askopenfilenames(
            parent=root,
            title="개인정보를 제거할 HWPX 파일 선택",
            filetypes=[("HWPX 파일", "*.hwpx"), ("HWP 파일", "*.hwp"), ("모든 파일", "*.*")],
        )
        if not files:
            root.attributes("-topmost", True)
            return

        btn.config(state="disabled", text="처리 중...")
        root.update()

        script = str(Path(__file__).parent / "hwpx_to_md_redactor.py")
        cmd = ["python", "-X", "utf8", script] + list(files)

        proc = subprocess.Popen(
            cmd,
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
        proc.wait()

        outputs = [f"  {Path(f).stem}_제거완.md" for f in files]
        messagebox.showinfo(
            "완료",
            f"처리 완료 ({len(files)}개 파일)\n\n" + "\n".join(outputs),
            parent=root,
        )
        btn.config(state="normal", text="HWPX 파일 선택 후 실행")
        root.attributes("-topmost", True)

    btn = tk.Button(
        root,
        text="HWPX 파일 선택 후 실행",
        font=("맑은 고딕", 11),
        command=select_and_run,
        width=22,
        height=2,
    )
    btn.pack(pady=5)

    root.lift()
    root.focus_force()
    root.mainloop()


if __name__ == "__main__":
    main()
