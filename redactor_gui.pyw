"""LegalDoc Redactor — GUI Launcher"""
import subprocess
import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path


def main():
    root = tk.Tk()
    root.title("HWPX 개인정보 제거기")
    root.geometry("400x150")
    root.attributes("-topmost", True)
    root.resizable(False, False)

    root.update_idletasks()
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    x = (sw - 400) // 2
    y = (sh - 150) // 2
    root.geometry(f"400x150+{x}+{y}")

    label = tk.Label(root, text="HWPX 개인정보 제거기", font=("맑은 고딕", 14, "bold"))
    label.pack(pady=(20, 10))

    def select_and_run():
        root.attributes("-topmost", False)
        files = filedialog.askopenfilenames(
            parent=root,
            title="개인정보를 제거할 HWPX 파일 선택",
            filetypes=[("HWPX 파일", "*.hwpx"), ("모든 파일", "*.*")],
        )
        if not files:
            root.attributes("-topmost", True)
            return

        btn.config(state="disabled", text="처리 중...")
        root.update()

        script = str(Path(__file__).parent / "hwpx_redactor.py")
        cmd = ["python", "-X", "utf8", script] + list(files)

        proc = subprocess.Popen(
            cmd,
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
        proc.wait()

        outputs = [f"  {Path(f).stem}_제거완.hwpx" for f in files]
        messagebox.showinfo(
            "완료",
            f"처리 완료 ({len(files)}개 파일)\n\n" + "\n".join(outputs),
            parent=root,
        )
        btn.config(state="normal", text="파일 선택 후 실행")

    btn = tk.Button(
        root,
        text="파일 선택 후 실행",
        font=("맑은 고딕", 11),
        command=select_and_run,
        width=20,
        height=2,
    )
    btn.pack(pady=5)

    root.lift()
    root.focus_force()
    root.mainloop()


if __name__ == "__main__":
    main()
