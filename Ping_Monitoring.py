import os
import json
import tkinter as tk
from tkinter import ttk, messagebox, Scrollbar
import requests
from ping3 import ping
from datetime import datetime

class MonitoringDashboard(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Network Health Monitor Pro")
        self.geometry("1440x900")
        self.configure(bg="#f0f2f5")
        
        # Configure style
        self.style = ttk.Style()
        self.style.theme_use('clam')
        self._configure_styles()
        
        # Initialize data from config
        self.services = self.load_config()
        
        # Create widgets
        self._create_widgets()
        self._layout_ui()
        self.update_checks()

    def load_config(self):
        config_path = os.path.join(os.path.dirname(__file__), 'config.json')
        try:
            with open(config_path) as f:
                config = json.load(f)
                return {
                    "Network Devices": config.get("ip_addresses", {}),
                    "Web Services": config.get("web_services", {})
                }
        except Exception as e:
            messagebox.showerror("Config Error", f"Failed to load config: {str(e)}")
            self.destroy()
            return {}

    def _configure_styles(self):
        self.style.configure('TFrame', background='#f0f2f5')
        self.style.configure('Header.TFrame', background='#2c3e50')
        self.style.configure('Card.TFrame', background='white', relief='groove')
        self.style.configure('Status.TLabel', background='white', font=('Segoe UI', 10))
        self.style.configure('Title.TLabel', background='#2c3e50', foreground='white', 
                           font=('Segoe UI', 12, 'bold'))
        self.style.configure('Green.TLabel', background='#2ecc71', foreground='white')
        self.style.configure('Red.TLabel', background='#e74c3c', foreground='white')
        self.style.configure('Yellow.TLabel', background='#f1c40f', foreground='black')
        self.style.map('TButton', background=[('active', '#3498db')], foreground=[('active', 'white')])

    def _create_widgets(self):
        # Header
        self.header = ttk.Frame(self, style='Header.TFrame')
        self.title_label = ttk.Label(self.header, text="NETWORK HEALTH MONITOR", style='Title.TLabel')
        self.last_update = ttk.Label(self.header, text="Last update: --:--:--", style='Title.TLabel')
        
        # Service panels
        self.notebook = ttk.Notebook(self)
        self.network_tab = ttk.Frame(self.notebook)
        self.web_tab = ttk.Frame(self.notebook)
        
        # Create service cards
        self.service_cards = {}
        for category in self.services:
            tab = self.network_tab if category == "Network Devices" else self.web_tab
            for service in self.services[category]:
                card = ttk.Frame(tab, style='Card.TFrame', padding=10)
                self.service_cards[service] = {
                    'frame': card,
                    'title': ttk.Label(card, text=service, font=('Segoe UI', 11, 'bold')),
                    'status': ttk.Label(card, text="Checking...", style='Status.TLabel'),
                    'time': ttk.Label(card, text="", style='Status.TLabel'),
                    'icon': ttk.Label(card, text="🔄", font=('Segoe UI', 24))
                }

        # Alert panel
        self.alert_panel = ttk.Frame(self, style='Card.TFrame')
        self.alert_title = ttk.Label(self.alert_panel, text="🔔 Active Alerts (0)", 
                                   font=('Segoe UI', 11, 'bold'))
        self.alert_list = tk.Listbox(self.alert_panel, bg='white', bd=0, 
                                   font=('Segoe UI', 10), height=8)
        self.alert_scroll = Scrollbar(self.alert_panel, orient="vertical")
        
        # Control panel
        self.control_panel = ttk.Frame(self, style='Card.TFrame')
        ttk.Button(self.control_panel, text="Refresh Now", command=self.update_checks).pack(pady=5)
        ttk.Button(self.control_panel, text="History Report", command=self.show_history).pack(pady=5)
        ttk.Button(self.control_panel, text="Settings", command=self.show_settings).pack(pady=5)

    def _layout_ui(self):
        # Header layout
        self.header.pack(fill='x', padx=10, pady=10)
        self.title_label.pack(side='left', padx=20)
        self.last_update.pack(side='right', padx=20)
        
        # Main content layout
        self.notebook.add(self.network_tab, text="🖧 Network Devices")
        self.notebook.add(self.web_tab, text="🌐 Web Services")
        self.notebook.pack(fill='both', expand=True, padx=10, pady=(0,10))
        
        # Service cards layout
        for category in self.services:
            tab = self.network_tab if category == "Network Devices" else self.web_tab
            row, col = 0, 0
            for service in self.services[category]:
                card = self.service_cards[service]
                card['frame'].grid(row=row, column=col, padx=10, pady=10, sticky='nsew')
                card['title'].grid(row=0, column=0, columnspan=2, sticky='w')
                card['icon'].grid(row=1, column=0, rowspan=2, padx=(0,10))
                card['status'].grid(row=1, column=1, sticky='w')
                card['time'].grid(row=2, column=1, sticky='w')
                col += 1
                if col > 2:
                    col = 0
                    row += 1
            for i in range(3):
                tab.grid_columnconfigure(i, weight=1)
        
        # Right sidebar layout
        self.alert_panel.pack(side='right', fill='y', padx=(0,10), pady=10)
        self.alert_title.pack(fill='x', padx=10, pady=10)
        self.alert_scroll.pack(side='right', fill='y')
        self.alert_list.pack(fill='both', expand=True, padx=10, pady=(0,10))
        self.alert_list.config(yscrollcommand=self.alert_scroll.set)
        self.alert_scroll.config(command=self.alert_list.yview)
        
        self.control_panel.pack(side='right', fill='y', padx=(0,10), pady=10)

    def update_status(self, service, status, response_time):
        card = self.service_cards[service]
        status_text = ""
        status_style = ""
        icon = "✅"
        
        if status == "success":
            status_text = f"Operational ({response_time}ms)"
            status_style = 'Green.TLabel'
            icon = "✅"
        elif status == "warning":
            status_text = f"Performance Degradation ({response_time}ms)"
            status_style = 'Yellow.TLabel'
            icon = "⚠️"
        else:
            status_text = "Service Unavailable"
            status_style = 'Red.TLabel'
            icon = "❌"
        
        card['status'].configure(text=status_text, style=status_style)
        card['time'].configure(text=f"Last check: {datetime.now().strftime('%H:%M:%S')}")
        card['icon'].configure(text=icon)
        self.last_update.configure(text=f"Last update: {datetime.now().strftime('%H:%M:%S')}")

    def update_checks(self):
        # Network devices check
        if "Network Devices" in self.services:
            for service, ip in self.services["Network Devices"].items():
                try:
                    response = ping(ip, timeout=2)
                    status = "success" if response else "error"
                    self.update_status(service, status, round((response or 0)*1000, 1))
                except Exception as e:
                    self.update_status(service, "error", 0)
        
        # Web services check
        if "Web Services" in self.services:
            for service, url in self.services["Web Services"].items():
                try:
                    start = datetime.now()
                    response = requests.get(url, timeout=5)
                    response_time = (datetime.now() - start).total_seconds() * 1000
                    status = "success" if response.ok else "warning"
                    self.update_status(service, status, round(response_time, 1))
                except Exception as e:
                    self.update_status(service, "error", 0)
        
        self.after(5000, self.update_checks)

    def show_history(self):
        messagebox.showinfo("History", "Historical reports feature coming soon!")

    def show_settings(self):
        messagebox.showinfo("Settings", "Configuration panel coming soon!")

if __name__ == "__main__":
    app = MonitoringDashboard()
    app.mainloop()