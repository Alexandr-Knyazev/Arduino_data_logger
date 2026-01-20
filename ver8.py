import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import csv
import time
import threading
import math
import queue
import random
from datetime import datetime
from typing import List, Tuple, Dict
import statistics
import platform


# ----- Симулятор Arduino -----

class ArduinoSimulator:
    def __init__(self):
        self.min_value = 0.0
        self.max_value = 100.0

    def read_data(self) -> float:
        return random.uniform(self.min_value, self.max_value)


# ----- Фильтрация и сглаживание -----

def sigma_filter_last(values: List[float],
                      window_size: int = 10,
                      k: float = 3.0) -> float:
    """
    Простой σ-фильтр по скользящему окну.
    Берём последние window_size значений, считаем среднее и σ.
    Если |x_last - mean| > k * σ, считаем точку выбросом и
    заменяем её на mean, иначе возвращаем исходное значение.

    Это классический "3σ-правило" в скользящем окне.
    """
    if not values:
        return float("nan")

    w = min(window_size, len(values))
    window = values[-w:]
    if len(window) < 2:
        return values[-1]

    mean = statistics.mean(window)
    std = statistics.pstdev(window)  # можно stdev, но pstdev проще

    if std == 0:
        return values[-1]

    x = values[-1]
    if abs(x - mean) > k * std:
        return mean
    return x


def moving_average(data: List[float], window_size: int = 5) -> List[float]:
    """Сглаживание данных методом скользящего среднего."""
    if len(data) <= 1:
        return data[:]

    result = []
    for i in range(len(data)):
        start = max(0, i - window_size // 2)
        end = min(len(data), i + window_size // 2 + 1)
        window = data[start:end]
        result.append(sum(window) / len(window))
    return result


# ----- Основное приложение -----

class ArduinoDataApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Arduino Data Logger")
        self.root.geometry("800x600")

        self.is_recording = False
        self.data_queue: "queue.Queue[Tuple[float, float]]" = queue.Queue()
        self.experiments: Dict[int, List[Tuple[float, float]]] = {}
        self.current_experiment = 1
        self.serial_port = None

        self.setup_gui()
        self.update_port_list()

    # ----- GUI -----

    def setup_gui(self):
        main_frame = ttk.Frame(self.root, padding="5")
        main_frame.grid(row=0, column=0, sticky="nsew")

        # Кнопки управления
        btn_frame = ttk.Frame(main_frame)
        btn_frame.grid(row=0, column=0, columnspan=3, pady=5)

        self.btn_start = tk.Button(
            btn_frame, text="Начать", command=self.start_experiment,
            bg="lightgreen", width=15
        )
        self.btn_start.pack(side=tk.LEFT, padx=2)

        self.btn_stop = tk.Button(
            btn_frame, text="Закончить", command=self.stop_experiment,
            bg="lightcoral", width=15, state=tk.DISABLED
        )
        self.btn_stop.pack(side=tk.LEFT, padx=2)

        tk.Button(
            btn_frame, text="Сохранить", command=self.save_all_data,
            bg="lightblue", width=15
        ).pack(side=tk.LEFT, padx=2)

        tk.Button(
            btn_frame, text="Открыть", command=self.open_data,
            bg="lightyellow", width=15
        ).pack(side=tk.LEFT, padx=2)

        tk.Button(
            btn_frame, text="Анализ", command=self.analyze_data,
            bg="lightcyan", width=15
        ).pack(side=tk.LEFT, padx=2)

        # Режим работы
        mode_frame = ttk.Frame(main_frame)
        mode_frame.grid(row=1, column=0, columnspan=3, pady=5)

        self.mode_var = tk.StringVar(value="simulator")
        tk.Radiobutton(
            mode_frame, text="Симулятор", variable=self.mode_var,
            value="simulator", command=self.update_port_list
        ).pack(side=tk.LEFT, padx=5)
        tk.Radiobutton(
            mode_frame, text="Arduino", variable=self.mode_var,
            value="real", command=self.update_port_list
        ).pack(side=tk.LEFT, padx=5)

        # Порт
        port_frame = ttk.Frame(main_frame)
        port_frame.grid(row=2, column=0, columnspan=3, pady=5)

        tk.Label(port_frame, text="Порт:").pack(side=tk.LEFT)
        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(port_frame, textvariable=self.port_var, width=15)
        self.port_combo.pack(side=tk.LEFT, padx=5)
        tk.Button(
            port_frame, text="↻", command=self.update_port_list,
            width=3
        ).pack(side=tk.LEFT)

        # Информация
        info_frame = ttk.Frame(main_frame)
        info_frame.grid(row=3, column=0, columnspan=3, pady=5)

        self.exp_label = tk.Label(
            info_frame, text=f"Эксперимент Э{self.current_experiment}",
            font=("Arial", 11)
        )
        self.exp_label.pack(side=tk.LEFT, padx=10)

        self.count_label = tk.Label(info_frame, text="Точек: 0")
        self.count_label.pack(side=tk.LEFT, padx=10)

        self.value_label = tk.Label(
            info_frame, text="Значение: --.--", font=("Arial", 10)
        )
        self.value_label.pack(side=tk.LEFT, padx=10)

        # Лог
        self.log_text = scrolledtext.ScrolledText(main_frame, height=15, width=85)
        self.log_text.grid(row=4, column=0, columnspan=3, pady=5, sticky="nsew")

        # Статус
        self.status_var = tk.StringVar(value="Готов. Режим: симулятор")
        tk.Label(
            main_frame, textvariable=self.status_var, relief=tk.SUNKEN,
            anchor=tk.W
        ).grid(row=5, column=0, columnspan=3, sticky="ew", pady=5)

        # Растяжение
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(4, weight=1)

    # ----- Порты -----

    def update_port_list(self):
        if self.mode_var.get() == "simulator":
            self.port_combo["values"] = ["Симулятор"]
            self.port_var.set("Симулятор")
            self.status_var.set("Готов. Режим: симулятор")
            return

        ports = []
        try:
            try:
                import serial.tools.list_ports  # pyserial
                ports = [p.device for p in serial.tools.list_ports.comports()]
            except ImportError:
                if platform.system() == "Windows":
                    ports = [f"COM{i}" for i in range(1, 11)]
                else:
                    ports = ["/dev/ttyUSB0", "/dev/ttyACM0"]
        except Exception:
            ports = []

        self.port_combo["values"] = ports
        if ports:
            self.port_var.set(ports[0])
            self.status_var.set(f"Найдено портов: {len(ports)}")
        else:
            self.port_var.set("")
            self.status_var.set("Порты не найдены")

    # ----- Управление экспериментами -----

    def start_experiment(self):
        if self.current_experiment not in self.experiments:
            self.experiments[self.current_experiment] = []

        if self.mode_var.get() == "real":
            port = self.port_var.get()
            if not port:
                messagebox.showerror("Ошибка", "Не выбран порт Arduino!")
                return
            try:
                import serial
            except ImportError:
                messagebox.showerror(
                    "Ошибка",
                    "Модуль pyserial не установлен.\n"
                    "Установите: pip install pyserial"
                )
                return

            try:
                self.serial_port = serial.Serial(port, 9600, timeout=1)
                time.sleep(2)
                self.serial_port.reset_input_buffer()
                self.status_var.set(f"Подключено к {port}")
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось подключиться:\n{e}")
                return

        self.is_recording = True
        self.btn_start.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL)

        self.log_text.insert(tk.END, f"\n{'=' * 50}\n")
        self.log_text.insert(tk.END, f"Начат эксперимент Э{self.current_experiment}\n")
        self.log_text.insert(tk.END, f"Время: {datetime.now().strftime('%H:%M:%S')}\n")
        self.log_text.insert(tk.END, f"{'=' * 50}\n")
        self.log_text.see(tk.END)

        threading.Thread(target=self.collect_data, daemon=True).start()
        self.update_display()

    def stop_experiment(self):
        self.is_recording = False

        if self.serial_port:
            try:
                self.serial_port.close()
            except Exception:
                pass
            self.serial_port = None

        self.btn_start.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.DISABLED)

        data = self.experiments.get(self.current_experiment, [])
        if data:
            values = [v for _, v in data]
            count = len(values)
            mean_val = sum(values) / count

            self.log_text.insert(
                tk.END,
                f"\nЭксперимент Э{self.current_experiment} завершен\n"
            )
            self.log_text.insert(
                tk.END,
                f"Точек: {count}, Среднее: {mean_val:.2f}\n"
            )
            self.log_text.insert(tk.END, f"{'=' * 50}\n")

        self.current_experiment += 1
        self.update_display_info()

    # ----- Сбор данных -----

    def collect_data(self):
        simulator = ArduinoSimulator()

        while self.is_recording:
            try:
                timestamp = time.time()

                if self.mode_var.get() == "real" and self.serial_port:
                    if self.serial_port.in_waiting:
                        line = self.serial_port.readline().decode(
                            "utf-8", errors="ignore"
                        ).strip()
                        try:
                            value = float(line.split(",")[0])
                        except ValueError:
                            continue
                    else:
                        time.sleep(0.01)
                        continue
                else:
                    value = simulator.read_data()

                # σ-фильтр по последнему окну значений
                exp_list = self.experiments[self.current_experiment]
                raw_values = [v for _, v in exp_list] + [value]
                filtered_value = sigma_filter_last(
                   raw_values, window_size=10, k=3.0
                )

                self.experiments[self.current_experiment].append(
                    (timestamp, filtered_value) # вместо filtered_value = value
                )
                self.data_queue.put((timestamp, filtered_value))

            except Exception as e:
                print(f"Ошибка при чтении данных: {e}")

            time.sleep(0.1)

    # ----- Обновление GUI -----

    def update_display(self):
        try:
            updated = False
            while not self.data_queue.empty():
                timestamp, value = self.data_queue.get_nowait()
                self.value_label.config(text=f"Значение: {value:.2f}")
                updated = True

                if random.random() < 0.2:
                    ts = datetime.fromtimestamp(timestamp).strftime("%H:%M:%S")
                    self.log_text.insert(
                        tk.END,
                        f"{ts} | Э{self.current_experiment}: {value:.2f}\n"
                    )
                    self.log_text.see(tk.END)

                self.update_display_info()

            if updated:
                self.root.update_idletasks()
        except Exception:
            pass

        if self.is_recording:
            self.root.after(100, self.update_display)

    def update_display_info(self):
        self.exp_label.config(text=f"Эксперимент Э{self.current_experiment}")
        if self.current_experiment in self.experiments:
            count = len(self.experiments[self.current_experiment])
            self.count_label.config(text=f"Точек: {count}")

    # ----- Сохранение/загрузка -----

    def save_all_data(self):
        if not self.experiments:
            messagebox.showwarning("Предупреждение", "Нет данных для сохранения!")
            return

        filename = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV файлы", "*.csv"), ("Все файлы", "*.*")]
        )
        if not filename:
            return

        try:
            with open(filename, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(["Эксперимент", "Время", "Значение", "Дата_и_время"])
                for exp_num in sorted(self.experiments.keys()):
                    for timestamp, value in self.experiments[exp_num]:
                        dt = datetime.fromtimestamp(timestamp).strftime(
                            "%Y-%m-%d %H:%M:%S"
                        )
                        writer.writerow([f"Э{exp_num}", timestamp, value, dt])

            messagebox.showinfo("Успех", f"Данные сохранены в {filename}")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось сохранить: {e}")

    def open_data(self):
        filename = filedialog.askopenfilename(
            filetypes=[("CSV файлы", "*.csv"), ("Все файлы", "*.*")]
        )
        if not filename:
            return

        try:
            self.experiments.clear()
            self.current_experiment = 1

            with open(filename, "r", encoding="utf-8-sig") as f:
                reader = csv.reader(f)
                header = next(reader, None)
                for row in reader:
                    if len(row) < 3:
                        continue
                    try:
                        exp_num = int(row[0].lstrip("Ээ"))
                        timestamp = float(row[1])
                        value = float(row[2])
                    except ValueError:
                        continue

                    self.experiments.setdefault(exp_num, []).append((timestamp, value))
                    if exp_num >= self.current_experiment:
                        self.current_experiment = exp_num + 1

            self.log_text.delete(1.0, tk.END)
            self.log_text.insert(tk.END, f"Загружено из {filename}\n")
            for exp_num in sorted(self.experiments.keys()):
                count = len(self.experiments[exp_num])
                self.log_text.insert(tk.END, f"Э{exp_num}: {count} точек\n")

            self.update_display_info()
            messagebox.showinfo("Успех", "Данные загружены!")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось загрузить: {e}")

    # ----- Анализ и график -----

    def analyze_data(self):
        if not self.experiments:
            messagebox.showwarning("Предупреждение", "Нет данных для анализа!")
            return

        select_window = tk.Toplevel(self.root)
        select_window.title("Выбор экспериментов")
        select_window.geometry("300x250")

        tk.Label(
            select_window,
            text="Выберите эксперименты для анализа:"
        ).pack(pady=10)

        check_vars: Dict[int, tk.BooleanVar] = {}
        for exp_num in sorted(self.experiments.keys()):
            var = tk.BooleanVar(value=True)
            check_vars[exp_num] = var
            count = len(self.experiments[exp_num])
            tk.Checkbutton(
                select_window,
                text=f"Эксперимент Э{exp_num} ({count} точек)",
                variable=var
            ).pack(anchor=tk.W, padx=20, pady=2)

        def do_analyze():
            selected = [exp for exp, var in check_vars.items() if var.get()]
            if not selected:
                messagebox.showwarning(
                    "Предупреждение", "Не выбрано ни одного эксперимента!"
                )
                return
            self.create_plot(selected)
            select_window.destroy()

        tk.Button(
            select_window, text="Анализировать",
            command=do_analyze, bg="lightblue", width=15
        ).pack(pady=10)
        tk.Button(
            select_window, text="Отмена",
            command=select_window.destroy, bg="lightcoral", width=15
        ).pack()

    def create_plot(self, experiment_numbers: List[int]):
        if not experiment_numbers:
            return

        plot_window = tk.Toplevel(self.root)
        plot_window.title("График экспериментов")
        plot_window.geometry("1000x700")

        canvas = tk.Canvas(plot_window, width=950, height=550, bg="white")
        canvas.pack(pady=10)

        info_label = tk.Label(
            plot_window,
            text="Наведите на точку для просмотра координат"
        )
        info_label.pack()

        colors = [
            "blue", "red", "green", "purple",
            "orange", "brown", "pink", "gray"
        ]
        all_points = []

        max_duration = 0.0
        experiment_data = []

        for exp_num in experiment_numbers:
            data = self.experiments.get(exp_num, [])
            if not data:
                continue

            start_time = data[0][0]
            times = [t - start_time for t, _ in data]
            values = [v for _, v in data]

            if times:
                duration = max(times)
                if duration == 0:
                    duration = 1.0
                max_duration = max(max_duration, duration)
            else:
                continue

            smooth_values = moving_average(values, window_size=min(7, len(values)))

            experiment_data.append({
                "num": exp_num,
                "times": times,
                "values": values,
                "smooth_values": smooth_values,
                "color": colors[exp_num % len(colors)]
            })

        if not experiment_data:
            messagebox.showwarning("Ошибка", "Нет данных для построения графика!")
            return

        if max_duration == 0:
            max_duration = 1.0

        padding_x = max_duration * 0.05
        padding_y = 10.0

        all_values = []
        for exp in experiment_data:
            all_values.extend(exp["values"])

        if not all_values:
            messagebox.showwarning("Ошибка", "Нет значений для построения графика!")
            return

        min_val = min(all_values)
        max_val = max(all_values)
        val_range = max_val - min_val
        if val_range <= 0:
            val_range = 1.0

        def to_canvas_x(t: float) -> float:
            return 50 + (t / (max_duration + 2 * padding_x)) * 850

        def to_canvas_y(v: float) -> float:
            return 500 - ((v - min_val + padding_y) /
                          (val_range + 2 * padding_y)) * 450

        # Оси
        canvas.create_line(50, 500, 900, 500, width=2)
        canvas.create_line(50, 50, 50, 500, width=2)

        # Деления
        for i in range(6):
            x_pos = 50 + 850 * i / 5
            time_val = max_duration * i / 5
            canvas.create_line(x_pos, 500, x_pos, 505, width=1)
            canvas.create_text(x_pos, 520, text=f"{time_val:.1f}с")

            y_pos = 500 - 450 * i / 5
            val_val = min_val + val_range * i / 5
            canvas.create_line(45, y_pos, 50, y_pos, width=1)
            canvas.create_text(30, y_pos, text=f"{val_val:.1f}")

        # Линии и легенда
        legend_y = 30
        for exp in experiment_data:
            color = exp["color"]
            times = exp["times"]
            values = exp["values"]
            smooth_values = exp["smooth_values"]

            points = []
            for t, v in zip(times, values):
                x = to_canvas_x(t)
                y = to_canvas_y(v)
                points.append((x, y))
                all_points.append((x, y, t, v, exp["num"]))

            if len(points) > 1:
                for i in range(len(points) - 1):
                    x1, y1 = points[i]
                    x2, y2 = points[i + 1]
                    canvas.create_line(x1, y1, x2, y2, fill=color, width=1)

            if len(times) == len(smooth_values) and len(times) > 1:
                smooth_points = []
                for t, v in zip(times, smooth_values):
                    x = to_canvas_x(t)
                    y = to_canvas_y(v)
                    smooth_points.append((x, y))

                for i in range(len(smooth_points) - 1):
                    x1, y1 = smooth_points[i]
                    x2, y2 = smooth_points[i + 1]
                    canvas.create_line(x1, y1, x2, y2, fill=color, width=3)

            canvas.create_text(
                800, legend_y, text=f"Э{exp['num']}",
                fill=color, font=("Arial", 10), anchor=tk.W
            )
            legend_y += 20

        canvas.create_text(
            475, 540,
            text="Время от начала эксперимента (секунды)",
            font=("Arial", 11)
        )
        canvas.create_text(
            15, 275, text="Значение",
            angle=90, font=("Arial", 11)
        )

        # Hover
        last_highlight = None

        def on_motion(event):
            nonlocal last_highlight
            if last_highlight:
                canvas.delete(last_highlight)
                last_highlight = None

            closest = None
            min_dist = 15

            for x, y, t_off, val, exp_num in all_points:
                dist = math.hypot(event.x - x, event.y - y)
                if dist < min_dist:
                    min_dist = dist
                    closest = (x, y, t_off, val, exp_num)

            if closest:
                x, y, t_off, val, exp_num = closest
                last_highlight = canvas.create_oval(
                    x - 4, y - 4, x + 4, y + 4,
                    fill="yellow", outline="black"
                )
                info_label.config(
                    text=f"Э{exp_num}: Время={t_off:.2f}с, Значение={val:.2f}"
                )
            else:
                info_label.config(
                    text="Наведите на точку для просмотра координат"
                )

        canvas.bind("<Motion>", on_motion)

        tk.Button(
            plot_window, text="Закрыть",
            command=plot_window.destroy, bg="lightcoral", width=15
        ).pack(pady=10)


def main():
    root = tk.Tk()
    app = ArduinoDataApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
