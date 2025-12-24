import Anthropic from '@anthropic-ai/sdk';
import { PatientData, ClinicMode } from '../types';
import * as fs from 'fs';
import * as path from 'path';

export class ClaudeService {
  private client: Anthropic;
  private templateCache: Map<ClinicMode, string> = new Map();

  constructor(apiKey: string) {
    this.client = new Anthropic({
      apiKey: apiKey,
    });
    this.loadTemplates();
  }

  /**
   * Загрузка готовых шаблонов из файлов
   */
  private loadTemplates(): void {
    try {
      const dinastiyaPath = path.join(__dirname, '../../data/templates/dinastiya_template.txt');
      const pskpPath = path.join(__dirname, '../../data/templates/pskp_template.txt');

      if (fs.existsSync(dinastiyaPath)) {
        const template = fs.readFileSync(dinastiyaPath, 'utf-8');
        this.templateCache.set(ClinicMode.DINASTIYA, template);
      }

      if (fs.existsSync(pskpPath)) {
        const template = fs.readFileSync(pskpPath, 'utf-8');
        this.templateCache.set(ClinicMode.PSKP, template);
      }

      console.log('✓ Templates loaded successfully');
    } catch (error) {
      console.error('Error loading templates:', error);
    }
  }

  /**
   * Получение готового шаблона для клиники
   */
  private getBaseTemplate(clinic: ClinicMode): string {
    return this.templateCache.get(clinic) || '';
  }

  /**
   * Распознавание текста с фото документа (OCR)
   * Использует Claude Sonnet для анализа изображений
   */
  async extractDataFromImage(imageBase64: string, mediaType: string): Promise<Partial<PatientData>> {
    try {
      const message = await this.client.messages.create({
        model: 'claude-3-5-sonnet-20241022',
        max_tokens: 1024,
        messages: [
          {
            role: 'user',
            content: [
              {
                type: 'image',
                source: {
                  type: 'base64',
                  media_type: mediaType as 'image/jpeg' | 'image/png' | 'image/gif' | 'image/webp',
                  data: imageBase64,
                },
              },
              {
                type: 'text',
                text: `Пожалуйста, извлеките из этого документа следующую информацию:
- ФИО (полное имя)
- Дата рождения (в формате ДД.ММ.ГГГГ)
- СНИЛС (если есть)

Ответьте в формате JSON:
{
  "fullName": "...",
  "birthDate": "...",
  "snils": "..."
}

Если какого-то поля нет, оставьте его пустым.`,
              },
            ],
          },
        ],
      });

      const response = message.content[0];
      if (response.type === 'text') {
        // Извлекаем JSON из ответа
        const jsonMatch = response.text.match(/\{[\s\S]*\}/);
        if (jsonMatch) {
          const data = JSON.parse(jsonMatch[0]);
          return {
            fullName: data.fullName || '',
            birthDate: data.birthDate || '',
            snils: data.snils || undefined,
          };
        }
      }

      return {};
    } catch (error) {
      console.error('Error extracting data from image:', error);
      throw error;
    }
  }

  /**
   * Генерация шаблона осмотра на основе готового шаблона
   * Использует Claude Opus для заполнения готового медицинского шаблона
   */
  async generateExaminationTemplate(
    patientData: PatientData,
    clinic: ClinicMode,
    archiveTemplates: string[] = []
  ): Promise<string> {
    try {
      const baseTemplate = this.getBaseTemplate(clinic);

      if (!baseTemplate) {
        throw new Error(`Шаблон для клиники ${clinic} не найден`);
      }

      let archiveContext = '';
      if (archiveTemplates.length > 0) {
        archiveContext = `\n\nПРИМЕРЫ ПОХОЖИХ ОСМОТРОВ ИЗ АРХИВА (для справки):\n${archiveTemplates.join('\n---\n')}`;
      }

      const message = await this.client.messages.create({
        model: 'claude-opus-4-20250514',
        max_tokens: 8192,
        messages: [
          {
            role: 'user',
            content: `Вы медицинский ассистент. Ваша задача - заполнить готовый шаблон медицинского осмотра.

ДАННЫЕ ПАЦИЕНТА:
- ФИО: ${patientData.fullName}
- Дата рождения: ${patientData.birthDate}
${patientData.snils ? `- СНИЛС: ${patientData.snils}` : ''}
- Диагноз: ${patientData.diagnosis}

ГОТОВЫЙ ШАБЛОН КЛИНИКИ:
${baseTemplate}

${archiveContext}

ИНСТРУКЦИИ:
1. Используйте ТОЧНО ЭТОТ шаблон, не изменяйте его структуру
2. Заполните все разделы шаблона реалистичными медицинскими данными
3. Вставьте данные пациента (ФИО, дата рождения, СНИЛС) в соответствующие места
4. Заполните разделы с учетом указанного диагноза: ${patientData.diagnosis}
5. Используйте примеры из архива как справочный материал для стиля заполнения
6. НЕ добавляйте лишний текст до или после шаблона
7. Верните ТОЛЬКО заполненный шаблон

Заполните шаблон:`,
          },
        ],
      });

      const response = message.content[0];
      if (response.type === 'text') {
        return response.text;
      }

      return '';
    } catch (error) {
      console.error('Error generating examination template:', error);
      throw error;
    }
  }

  /**
   * Исправление шаблона на основе комментариев пользователя
   */
  async correctTemplate(
    currentTemplate: string,
    patientData: PatientData,
    corrections: string
  ): Promise<string> {
    try {
      const message = await this.client.messages.create({
        model: 'claude-opus-4-20250514',
        max_tokens: 8192,
        messages: [
          {
            role: 'user',
            content: `Вы медицинский ассистент. У вас есть готовый шаблон медицинского осмотра, который нужно исправить.

ТЕКУЩИЙ ШАБЛОН:
${currentTemplate}

ДАННЫЕ ПАЦИЕНТА:
- ФИО: ${patientData.fullName}
- Дата рождения: ${patientData.birthDate}
${patientData.snils ? `- СНИЛС: ${patientData.snils}` : ''}
- Диагноз: ${patientData.diagnosis}

КОММЕНТАРИИ ДЛЯ ИСПРАВЛЕНИЯ:
${corrections}

ИНСТРУКЦИИ:
1. Внимательно прочитайте комментарии пользователя
2. Внесите необходимые исправления в шаблон
3. Сохраните структуру и формат шаблона
4. НЕ добавляйте лишний текст до или после шаблона
5. Верните ТОЛЬКО исправленный шаблон

Исправленный шаблон:`,
          },
        ],
      });

      const response = message.content[0];
      if (response.type === 'text') {
        return response.text;
      }

      return '';
    } catch (error) {
      console.error('Error correcting template:', error);
      throw error;
    }
  }

}
