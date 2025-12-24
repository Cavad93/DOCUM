import Anthropic from '@anthropic-ai/sdk';
import { PatientData, ClinicMode } from '../types';
import * as fs from 'fs';
import * as path from 'path';

export class ClaudeService {
  private client: Anthropic;

  constructor(apiKey: string) {
    this.client = new Anthropic({
      apiKey: apiKey,
    });
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
   * Генерация шаблона осмотра
   * Использует Claude Opus для создания детального медицинского шаблона
   */
  async generateExaminationTemplate(
    patientData: PatientData,
    clinic: ClinicMode,
    archiveTemplates: string[] = []
  ): Promise<string> {
    try {
      const clinicTemplates = this.getClinicTemplateInstructions(clinic);

      let archiveContext = '';
      if (archiveTemplates.length > 0) {
        archiveContext = `\n\nАрхивные шаблоны для справки:\n${archiveTemplates.join('\n---\n')}`;
      }

      const message = await this.client.messages.create({
        model: 'claude-opus-4-20250514',
        max_tokens: 4096,
        messages: [
          {
            role: 'user',
            content: `Создайте шаблон медицинского осмотра для клиники "${clinic}".

Данные пациента:
- ФИО: ${patientData.fullName}
- Дата рождения: ${patientData.birthDate}
${patientData.snils ? `- СНИЛС: ${patientData.snils}` : ''}
- Диагноз: ${patientData.diagnosis}

${clinicTemplates}
${archiveContext}

Пожалуйста, создайте полный шаблон медицинского осмотра с учетом диагноза и стандартов клиники.`,
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
   * Получение инструкций для конкретной клиники
   */
  private getClinicTemplateInstructions(clinic: ClinicMode): string {
    const templates: Record<ClinicMode, string> = {
      [ClinicMode.DINASTIYA]: `
Клиника "Династия" использует следующую структуру осмотра:

1. АНАМНЕЗ
   - Жалобы
   - История заболевания
   - Перенесенные заболевания
   - Аллергологический анамнез

2. ОБЪЕКТИВНЫЙ ОСМОТР
   - Общее состояние
   - Кожные покровы
   - Лимфатические узлы
   - Система дыхания
   - Сердечно-сосудистая система
   - Пищеварительная система
   - Мочевыделительная система

3. ПРЕДВАРИТЕЛЬНЫЙ ДИАГНОЗ

4. ПЛАН ОБСЛЕДОВАНИЯ

5. ПЛАН ЛЕЧЕНИЯ

6. РЕКОМЕНДАЦИИ
`,
      [ClinicMode.PSKP]: `
Клиника "ПСКП" использует следующую структуру осмотра:

1. ЖАЛОБЫ И АНАМНЕЗ
   - Основные жалобы
   - Anamnesis morbi
   - Anamnesis vitae
   - Эпидемиологический анамнез

2. СТАТУС PRAESENS
   - Общее состояние и сознание
   - Положение пациента
   - Телосложение и питание
   - Кожа и видимые слизистые
   - Подкожно-жировая клетчатка
   - Лимфоузлы
   - Мышцы и кости
   - Органы дыхания
   - Сердечно-сосудистая система
   - Органы пищеварения
   - Мочеполовая система
   - Нервная система

3. ДИАГНОЗ

4. ОБСЛЕДОВАНИЕ

5. НАЗНАЧЕНИЯ

6. ДНЕВНИКИ НАБЛЮДЕНИЯ
`,
    };

    return templates[clinic];
  }
}
