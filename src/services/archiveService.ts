import * as fs from 'fs';
import * as path from 'path';
import { ExaminationTemplate, PatientData, ClinicMode } from '../types';

export class ArchiveService {
  private archivePath: string;

  constructor(archivePath: string = './data/archive') {
    this.archivePath = archivePath;
    this.ensureArchiveExists();
  }

  /**
   * Создает директорию архива если её нет
   */
  private ensureArchiveExists(): void {
    if (!fs.existsSync(this.archivePath)) {
      fs.mkdirSync(this.archivePath, { recursive: true });
    }
  }

  /**
   * Сохранение шаблона в архив
   */
  async saveTemplate(template: ExaminationTemplate): Promise<void> {
    try {
      const filename = this.generateFilename(template);
      const filepath = path.join(this.archivePath, filename);

      await fs.promises.writeFile(filepath, JSON.stringify(template, null, 2), 'utf-8');
      console.log(`Template saved: ${filename}`);
    } catch (error) {
      console.error('Error saving template:', error);
      throw error;
    }
  }

  /**
   * Поиск шаблонов по параметрам
   */
  async findTemplates(
    patientData: Partial<PatientData>,
    clinic?: ClinicMode
  ): Promise<ExaminationTemplate[]> {
    try {
      const files = await fs.promises.readdir(this.archivePath);
      const templates: ExaminationTemplate[] = [];

      for (const file of files) {
        if (!file.endsWith('.json')) continue;

        const filepath = path.join(this.archivePath, file);
        const content = await fs.promises.readFile(filepath, 'utf-8');
        const template: ExaminationTemplate = JSON.parse(content);

        // Проверка совпадения параметров
        let matches = true;

        if (clinic && template.clinic !== clinic) {
          matches = false;
        }

        if (patientData.fullName && template.patientData.fullName !== patientData.fullName) {
          matches = false;
        }

        if (patientData.diagnosis && template.patientData.diagnosis !== patientData.diagnosis) {
          matches = false;
        }

        if (matches) {
          templates.push(template);
        }
      }

      return templates;
    } catch (error) {
      console.error('Error finding templates:', error);
      return [];
    }
  }

  /**
   * Получение похожих шаблонов для использования в качестве контекста
   */
  async getSimilarTemplates(diagnosis: string, clinic: ClinicMode, limit: number = 3): Promise<string[]> {
    try {
      const templates = await this.findTemplates({ diagnosis }, clinic);

      return templates.slice(0, limit).map((t) => t.content);
    } catch (error) {
      console.error('Error getting similar templates:', error);
      return [];
    }
  }

  /**
   * Генерация имени файла для шаблона
   */
  private generateFilename(template: ExaminationTemplate): string {
    const date = new Date().toISOString().split('T')[0];
    const safeName = template.patientData.fullName.replace(/[^a-zа-я0-9]/gi, '_');
    return `${template.clinic}_${safeName}_${date}_${template.id}.json`;
  }

  /**
   * Получение статистики архива
   */
  async getStatistics(): Promise<{
    total: number;
    byClinic: Record<string, number>;
  }> {
    try {
      const files = await fs.promises.readdir(this.archivePath);
      const byClinic: Record<string, number> = {};
      let total = 0;

      for (const file of files) {
        if (!file.endsWith('.json')) continue;

        const filepath = path.join(this.archivePath, file);
        const content = await fs.promises.readFile(filepath, 'utf-8');
        const template: ExaminationTemplate = JSON.parse(content);

        total++;
        byClinic[template.clinic] = (byClinic[template.clinic] || 0) + 1;
      }

      return { total, byClinic };
    } catch (error) {
      console.error('Error getting statistics:', error);
      return { total: 0, byClinic: {} };
    }
  }
}
