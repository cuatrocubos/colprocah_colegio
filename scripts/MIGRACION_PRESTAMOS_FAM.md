# Migración de préstamos personales FAM a Lending

Hallazgos y procedimiento para pasar la cartera de préstamos personales del Fondo de Ayuda Mutua (FAM),
registrada hasta ahora solo en contabilidad, al módulo Lending (ERPNext v16, Lending 16.8.1).

Análisis hecho en el sitio de desarrollo (`sac.colprocah.com.localhost`) con datos hasta julio 2026,
corte de prueba **30-06-2026**. La ejecución real se hará contra producción con su último cierre de mes.

## 1. Por qué no sirve la importación estándar de Lending

La plantilla de Data Import de Loan (tabla *Detalles de Importación de Préstamo*) no es adecuada para FAM:

- **Asiento de apertura con signo invertido.** Al validar un préstamo importado, Lending debita la
  *Payment Account* del producto (el banco 11002003) y acredita la *Loan Account* (11301022) por el
  capital pendiente. Deja al colegiado con saldo acreedor e infla el banco.
- **Bloqueo por cierres de período.** Ese asiento es de apertura (`is_opening = Yes`) y ERPNext no permite
  asientos de apertura en una empresa con *Period Closing Voucher* validado. FAM tiene cierres 2023, 2024 y 2025.
- **Todo el capital vence de una vez.** La importación crea una *Loan Demand* EMI/Principal por todo el
  capital pendiente a la fecha de migración: el préstamo aparece en mora desde el primer día.
- **Condiciones en el lugar equivocado.** En préstamos a plazo Lending toma método, períodos y fecha de
  inicio del propio Loan, no de las columnas de *Detalles de Importación*.
- **Filas omitidas reportadas como "Éxito".** Si en la vista previa se marca "Omitir fila", el importador
  no intenta nada y Frappe reporta 0 de 0 como éxito, sin registro.
- En desarrollo, además, el Property Setter `autoname = naming_series:` de Loan no tiene el campo
  `naming_series`, y los préstamos nuevos no se pueden nombrar si no se asigna a mano.

**Estrategia adoptada:** crear cada préstamo como préstamo normal (Loan + Loan Disbursement + Loan Repayment)
con su historia real, **sin asientos contables**, y luego un solo asiento de reclasificación.

## 2. Cómo están registrados hoy los préstamos

### En FAM, cuenta `11301001 - PRESTAMOS PERSONALES` (sin tercero)

| Movimiento | Registro |
|---|---|
| Desembolso | Dr 11301001 monto del préstamo / Cr 40003002 comisión (~3 %) / Cr banco 11002003 neto. Comentario con el número `AU-RES-NNN-PREST-NNN/AA`, el nombre y a veces el carné. |
| Refinanciamiento | En el mismo asiento del nuevo préstamo, Cr 11301001 por el saldo del anterior ("Y CANCELACION DE PRESTAMO NNN/AA"). |
| Pago directo a FAM | Cr 11301001 por la **cuota completa** (capital + interés), por ejemplo "PAGO CUOTA DE PRESTAMO # 048/25". |
| Intereses | Partida global de fin de año: Dr 11301001 / Cr 40001005 (p. ej. PD-FAM-2026-000061, 30-12-2025, L 307,784.39 por todo 2025). |
| Traslados | COLPROCAH transfiere sumas redondas ("TRASLADO DE FDO GRAL A FDO PRESTAMOS FAM", L 150,000–400,000) y deducciones de empleados ("DEDUCCION EMPLEADOS"): Cr 11301001 sin detalle por préstamo. |
| Apertura | PD-FAM-2023-000001 (01-01-2023): L 2,391,271.70, "SALDOS AL 31 DE DICIEMBRE DEL 2022", sin desglose. |

Consecuencias:

- El saldo de 11301001 **no es solo capital**: incluye los intereses cobrados en el año en curso (que salen
  hasta la partida de fin de año) y depende de cuándo COLPROCAH traslada lo cobrado.
- La cuenta también tiene **partidas intercompañía**: cuentas por cobrar al Fondo General (p. ej.
  L 614,064.32 y L 901,107.79 en 2023), el préstamo al Fondo General para las prestaciones de una empleada
  (L 231,714.91, 26-09-2023, abonado con venta de timbres) y una baja por fallecimiento (L 22,837.52).
- Los números de préstamo vienen en formatos variados (`PREST-048/25`, `PREST045/25`, `014-26`,
  `019/2026`, `00/25`, `AU-RES-PREST-252-069/23`) y algunos pagos no lo traen.

### En COLPROCAH, facturas `PREST-PER`

La mayoría de los colegiados paga en COLPROCAH: cada cuota se factura (Sales Invoice, artículo `PREST-PER`
"PRESTAMO PERSONAL", contra `20001004 - CXPAGAR FAM PRESTAMOS`) y se cobra con Payment Entry.
1,045 líneas, L 3,855,958.90; la facturación empezó en 2024 (5 líneas en 2023). Las facturas son por
cliente pero **sin número de préstamo**: se asignan por cliente y monto de cuota.

### Condiciones de los préstamos

El plan legado es amortización francesa al **17 % anual, tasa/12 por mes** (no por días reales).
Ejemplos: 30,000 a 12 meses = 2,736.14; 40,000 a 12 = 3,648.19; 60,000 a 18 = 3,799.81.
Los colegiados suelen redondear la cuota (2,737).

## 3. Reconstrucción (script `fam_legacy_loans.py`)

1. **Desembolsos** en 11301001 → número de préstamo, fecha, monto, comisión, colegiado (por carné o por
   nombre) y préstamos que cancela.
2. **Otros movimientos** de 11301001 → pagos directos, cancelaciones, refinanciamientos y devoluciones,
   por número de préstamo o, si falta, por colegiado. Un movimiento que nombra un préstamo anterior a 2023
   no se asigna al préstamo vigente del colegiado.
3. **Facturas PREST-PER** → al préstamo abierto del cliente cuya cuota se ajusta al monto.
4. **Plan y saldo**: plazo deducido de la cuota (17 %), saldo legado al corte.
5. Solo se reconstruye un préstamo si **la evidencia es completa**:
   - plazo deducible al 17 %,
   - primer pago a no más de 70 días del desembolso,
   - no faltan más de 2 cuotas hasta el corte,
   - sigue abierto (saldo > 1 % del monto y menos pagos que plazo).

   Cada préstamo excluido queda en `loans.csv` con su `skip_reason`.

En Lending: un Loan del producto *Prestamo FAM Empleados*, desembolso sin cargos (la comisión ya está en
40003002) y cada pago como *Normal Repayment* en su fecha, procesando antes intereses y cuotas. El primer
vencimiento se elige para que los pagos caigan en o después de su fecha. Los documentos de Lending **no
generan asientos** (se desactiva solo dentro del script).

### Diferencias Lending vs. plan legado

Lending calcula interés por días reales /365 y la primera cuota solo por los días desde el desembolso; el
plan legado cobra un mes completo. Ejemplo 048/25 (30,000, 8 cuotas pagadas): capital pendiente Lending
10,406.09 vs. legado 10,561.38. En los 25 préstamos creados por el script la diferencia total es de
L 592.68 (Lending 838,977.33 vs. legado 839,570.01).

Si el primer vencimiento cae después de un pago (p. ej. un mes exacto después del desembolso cuando el
colegiado paga a fin de mes), Lending aplica ese pago todo a capital y el préstamo queda una cuota atrasado;
con *Advance Payment* reprograma el plan en cada pago. Por eso el vencimiento se ajusta a los pagos reales.

## 4. Resultados en desarrollo (corte 30-06-2026)

| | Préstamos | Capital |
|---|---|---|
| Desembolsos encontrados 2023–2026 | 163 (todos con cliente) | |
| Abiertos al corte (plan legado) | 87 | 2,907,145.96 |
| Reconstruidos en Lending | 26 | 849,383.42 |
| Saldo de 11301001 al corte | | 3,236,972.48 |

Abiertos no reconstruidos:

| Motivo | Préstamos |
|---|---|
| Sin pagos encontrados (incluye préstamos de empleados) | 26 |
| Cuota no corresponde a un plazo al 17 % | 13 |
| Faltan pagos iniciales (sobre todo préstamos 2023) | 12 |
| Prácticamente pagados (saldo por redondeo) | 7 |
| Faltan demasiadas cuotas | 3 |

Préstamos creados: ACC-LOAN-2026-00004 (048/25) y ACC-LOAN-2026-00005 a 00029.

**Asiento de reclasificación** PD-FAM-2026-000283, 30-06-2026, **en borrador**:

| Cuenta | Debe | Haber |
|---|---|---|
| 11301022 PRESTAMOS PERSONALES (una línea por colegiado) | 849,383.42 | |
| 40001005 INTERESES S/PRESTAMOS (intereses 2026 de esos préstamos) | | 60,981.80 |
| 11301001 PRESTAMOS PERSONALES (legado) | | 788,401.62 |

Decisión: la diferencia no explicada **se queda en 11301001** (L 2,448,570.86 después del asiento).

## 5. Lo que no se puede reconstruir desde la contabilidad

- **Préstamos anteriores a 2023**: están en la apertura global; algunos siguen pagándose (866/15, 035/21).
- **Préstamos de empleados de COLPROCAH**: se pagan por planilla en traslados globales, sin detalle por
  persona (no hay HRMS en el sitio).
- **Préstamos sin rastro de pago** (p. ej. 023/24, L 120,000).
- **033/24** se trasladó a `11301019 CXC DINA MARLENY OSORIO` el 14-05-2025; el reporte aún lo muestra
  abierto porque el traslado se clasifica como global.

Para estos se necesitan los saldos de las hojas de control por préstamo que lleva FAM.

## 6. Clientes de empleados

Los 11 préstamos sin cliente eran de cinco empleadas de COLPROCAH (clientes duplicados con nombre corto o
inexistentes) y un comentario "COLPROCAH/NOMBRE" que el script no leía. `fix_fam_loan_customers.py`
fusiona los duplicados, pone el nombre completo y crea los clientes faltantes (grupo *Individual*).
Ninguno de esos préstamos quedó reconstruible: se pagan por planilla.

## 7. Procedimiento en producción

Desde el directorio `sites` del bench. Todo es simulación (se revierte) salvo `--commit`.

1. Respaldo de la base de datos.
2. Producto *Prestamo FAM Empleados*: `validate_normal_repayment = 0` (los colegiados pagan cuotas
   redondeadas; el script se detiene si está activo).
3. Clientes:
   `../env/bin/python -W ignore ../apps/colprocah_colegio/scripts/fix_fam_loan_customers.py --site <sitio> [--commit]`
4. Reporte, revisar `loans.csv`, `events.csv`, `unmatched.csv`:
   `../env/bin/python -W ignore ../apps/colprocah_colegio/scripts/fam_legacy_loans.py report --site <sitio> --cutover <AAAA-MM-DD> --out <dir>`
5. Crear préstamos (revisar `build.csv`):
   `... fam_legacy_loans.py build --site <sitio> --cutover <fecha> --out <dir> [--commit]`
6. Asiento de reclasificación en borrador (`--submit` para validarlo):
   `... fam_legacy_loans.py reclass --site <sitio> --cutover <fecha> --out <dir> [--commit] [--submit]`
7. Desde el corte, Lending registra todo: procesar intereses y cuotas desde la fecha de corte y registrar
   los pagos nuevos como Loan Repayment normales.

`build` omite préstamos ya existentes (por número de préstamo) y `reclass` se niega a crear un segundo
asiento. Los CSV contienen datos de colegiados: no se suben al repositorio.

## 8. Otros scripts

- `cancel_loan.py <loan> [--commit]`: anula un préstamo reconstruido y todos sus documentos de Lending sin
  intentar revertir asientos (no los tiene).
- `rebuild_048_25.py`: primera reconstrucción manual de 048/25; la reemplaza `fam_legacy_loans.py build`.

## 9. Pendientes

- Saldos al corte de los préstamos no reconstruibles (hojas de control de FAM).
- Tratamiento de las partidas intercompañía y de los intereses del año que quedan en 11301001.
- Recargo por mora: Lending no cobró recargo en cuotas pagadas con atraso (producto: 24 %, 5 días de
  gracia); no se investigó.
- En producción el campo `naming_series` de Loan existe (personalizado); el script solo asigna la serie
  cuando falta el campo.
