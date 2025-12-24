export interface PatientData {
  fullName: string;
  birthDate: string;
  snils?: string;
  diagnosis: string;
}

export interface ExaminationTemplate {
  id: string;
  clinic: 'dinastiya' | 'pskp';
  patientData: PatientData;
  content: string;
  createdAt: Date;
}

export enum ClinicMode {
  DINASTIYA = 'dinastiya',
  PSKP = 'pskp'
}

export interface BotContext {
  clinic: ClinicMode;
  awaitingData: boolean;
  patientData?: Partial<PatientData>;
}
