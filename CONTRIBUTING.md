# Contributing to Antigravity Vault

Thank you for your interest in contributing to **Antigravity Vault**! We welcome contributions, bug fixes, feature requests, and documentation improvements from the open-source community.

---

## 🛠️ Development Setup

1. **Clone the Repository:**
   ```bash
   git clone https://github.com/rishwebb/antigravity-vault.git
   cd antigravity-vault
   ```

2. **Run Locally:**
   - **Windows:** Double-click `install_and_run.bat` or run `python server.py`
   - **Linux / macOS:** Run `./install_and_run.sh`

3. **Verify API Endpoints:**
   ```bash
   python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:4848/api/summary').read().decode())"
   ```

---

## 🤝 Contribution Guidelines

1. **Fork the repo** and create your feature branch: `git checkout -b feature/amazing-feature`
2. **Ensure zero external paid dependencies:** Core modules should rely on standard Python libraries (`sqlite3`, `http.server`, `urllib`, `threading`, `hashlib`) so the system runs universally out-of-the-box.
3. **Commit your changes:** Use clear, descriptive commit messages.
4. **Push to the branch:** `git push origin feature/amazing-feature`
5. **Open a Pull Request!**

---

## 📄 License
By contributing to Antigravity Vault, you agree that your contributions will be licensed under the [MIT License](LICENSE).
