# Migración de préstamos personales FAM a Lending

Hallazgos, decisión y plan para pasar los préstamos personales del Fondo de Ayuda Mutua (FAM), registrados
hasta ahora solo en contabilidad, al módulo Lending (ERPNext v16, Lending 16.8.1).

Análisis hecho en el sitio de desarrollo (`sac.colprocah.com.localhost`) con datos hasta julio 2026,
corte de prueba **30-06-2026**. La ejecución real se hará contra producción con su último cierre de mes.

## Decisión

**Solo se migran los préstamos desembolsados en 2026.** Migrar toda la cartera vigente resultó
contraproducente: la mayoría de los préstamos anteriores no tiene un rastro de pagos completo en el sistema
(apertura global de 2022, facturación por préstamo desde 2024, deducciones de planilla en traslados
globales, partidas intercompañía en la misma cuenta), y reconstruirlos exigiría saldos externos préstamo por
préstamo. Los préstamos anteriores a 2026 siguen en `11301001` con el flujo actual hasta que se cancelen.

La prueba con toda la cartera (secciones 4 y 5) se descartó: en desarrollo se anularon los 26 préstamos
reconstruidos y el préstamo de prueba 001/26, y se eliminó el asiento de reclasificación en borrador.

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

**Estrategia:** crear cada préstamo 2026 como préstamo normal (Loan + Loan Disbursement + Loan Repayment)
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

### En COLPROCAH, facturas mensuales con `PREST-PER`

La factura mensual de cada colegiado la genera su **Subscription** de ERPNext (`CUOTA-…`, el día 1 a las
00:52) con cuota ordinaria, CO-FAM, seguro de vida, etc. Ninguna suscripción tiene plan de préstamo: el
personal **agrega a mano** la línea `PREST-PER` "PRESTAMO PERSONAL" (contra `20001004 - CXPAGAR FAM
PRESTAMOS`) y la de ahorro CACPROCAL. Cuando llega el depósito (en su mayoría a la cuenta Occidente
`OCC-11-401-011468-4`) se registra un Payment Entry por toda la factura y la fecha de la factura pasa a la
del pago (la original queda en `custom_original_posting_date`).

1,045 líneas `PREST-PER`, L 3,855,958.90; la facturación de préstamos empezó en 2024. Las líneas son por
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
   - desembolsado desde `--since` (para la migración: `2026-01-01`),
   - plazo deducible al 17 %,
   - primer pago a no más de 70 días del desembolso,
   - no faltan más de 2 cuotas hasta el corte,
   - sigue abierto (saldo > 1 % del monto y menos pagos que plazo).

   Se analizan todos los préstamos (los de 2026 refinancian anteriores), pero cada préstamo excluido queda
   en `loans.csv` con su `skip_reason`.

En Lending: un Loan del producto *Prestamo FAM Empleados*, desembolso sin cargos (la comisión ya está en
40003002) y cada pago como *Normal Repayment* en su fecha, procesando antes intereses y cuotas. El primer
vencimiento se elige para que los pagos caigan en o después de su fecha. Los documentos de Lending **no
generan asientos** (se desactiva solo dentro del script).

### Diferencias Lending vs. plan legado

Lending calcula interés por días reales /365 y la primera cuota solo por los días desde el desembolso; el
plan legado cobra un mes completo. Ejemplo 048/25 (30,000, 8 cuotas pagadas): capital pendiente Lending
10,406.09 vs. legado 10,561.38. En la prueba con 25 préstamos la diferencia total fue de L 592.68
(Lending 838,977.33 vs. legado 839,570.01).

Si el primer vencimiento cae después de un pago (p. ej. un mes exacto después del desembolso cuando el
colegiado paga a fin de mes), Lending aplica ese pago todo a capital y el préstamo queda una cuota atrasado;
con *Advance Payment* reprograma el plan en cada pago. Por eso el vencimiento se ajusta a los pagos reales.

## 4. Préstamos 2026 (desarrollo, corte 30-06-2026)

20 préstamos desembolsados en 2026:

| Situación | Préstamos |
|---|---|
| Reconstruibles: 005/26 a 013/26 (capital legado L 474,589.20) | 9 |
| Sin pagos hasta el corte: 001/26, 002/26, 015/26, 016/26, 017/26 y los de empleadas 004/26 y 014/26 (planilla) | 7 |
| Cuota no corresponde a 17 %: 003/26 (readecuación de deuda, L 46,388.93) | 1 |
| Desembolsados después del corte: 018/26 a 020/26 | 3 |

016/26, 017/26 y 002/26 tienen su primer pago en julio: con un corte de producción posterior deberían
entrar. El comentario de 018/26 dice que cancela 035/25, que ya canceló 016/26; probablemente se refiere a
003/26 (mismo colegiado) y hay que confirmarlo con FAM.

## 5. Prueba con toda la cartera (descartada)

| | Préstamos | Capital |
|---|---|---|
| Desembolsos encontrados 2023–2026 | 163 (todos con cliente) | |
| Abiertos al corte (plan legado) | 87 | 2,907,145.96 |
| Reconstruibles con evidencia completa | 26 | 849,383.42 |
| Saldo de 11301001 al corte | | 3,236,972.48 |

Abiertos no reconstruibles: sin pagos encontrados 26, cuota que no corresponde a 17 % 13, faltan pagos
iniciales 12, prácticamente pagados 7, faltan demasiadas cuotas 3. Tampoco se pueden reconstruir desde la
contabilidad los préstamos anteriores a 2023 (algunos siguen pagándose: 866/15, 035/21), los de empleados
de COLPROCAH (planilla sin detalle; no hay HRMS en el sitio) ni los sin rastro de pago (p. ej. 023/24,
L 120,000). 033/24 se trasladó a `11301019 CXC DINA MARLENY OSORIO` el 14-05-2025.

El asiento de reclasificación de esa prueba habría sido Dr 11301022 849,383.42 / Cr 40001005 60,981.80 /
Cr 11301001 788,401.62, dejando L 2,448,570.86 en 11301001.

### Clientes de empleados

Los 11 préstamos sin cliente eran de cinco empleadas de COLPROCAH (clientes duplicados con nombre corto o
inexistentes) y un comentario "COLPROCAH/NOMBRE" que el script no leía. `fix_fam_loan_customers.py`
fusiona los duplicados, pone el nombre completo y crea los clientes faltantes (grupo *Individual*).
Aplicado en desarrollo; falta en producción.

## 6. Plan para los pagos cobrados en COLPROCAH

Objetivo: que cada cuota de un préstamo Lending cobrada en COLPROCAH quede como *Loan Repayment* en FAM,
por préstamo y en la fecha del pago, con la deuda COLPROCAH → FAM controlada hasta el traslado.

1. **Artículo propio para préstamos Lending.** Nuevo artículo (p. ej. `PREST-FAM`) contra una cuenta de
   pasivo nueva en COLPROCAH ("CXP FAM préstamos Lending"). La línea lleva un campo personalizado con el
   Loan de FAM, obligatorio y validado (el préstamo debe ser del mismo cliente). Los préstamos anteriores a
   2026 siguen con `PREST-PER` y el flujo actual: los dos saldos no se mezclan.
2. **Repago automático en FAM.** Al validar un Payment Entry de COLPROCAH que paga una factura con líneas
   `PREST-FAM`, un hook de `colprocah_colegio` crea en FAM un Loan Repayment por línea, con la fecha del
   pago; al anular el Payment Entry se anula el repago. El repago usa un Modo de Pago "Cobro COLPROCAH" cuya
   cuenta en FAM es una cuenta por cobrar intercompañía ("CXC COLPROCAH – cobros de préstamos"); Lending ya
   toma la cuenta del Modo de Pago. En FAM: Dr CXC COLPROCAH / Cr capital e intereses.
3. **Traslados.** Las transferencias globales liquidan la cuenta intercompañía: en COLPROCAH Dr la CXP
   nueva / Cr banco; en FAM Dr banco / Cr CXC COLPROCAH. Un reporte mensual concilia la CXC de FAM con la
   CXP de COLPROCAH.
4. **Facturación automática (fase 2).** Agregar la línea `PREST-FAM` a la factura mensual a partir de las
   cuotas pendientes del préstamo en Lending, en lugar de digitarla.
5. **Planilla y deducciones de patronos.** Para empleados de COLPROCAH y remesas de patronos, importación
   mensual de Loan Repayments con el mismo Modo de Pago (sin HRMS no hay deducción automática). Los pagos
   directos al banco de FAM se registran como Loan Repayment normales.
6. **Vencimientos.** Los colegiados pagan durante el mes después de facturado el día 1; con recargo de
   24 % y 5 días de gracia, definir el día de vencimiento (o una gracia mayor) para no cobrar recargo a quien
   paga a tiempo. Verificar antes por qué la prueba no generó recargos.

Decisiones pendientes: tratamiento de pagos parciales de la factura mensual (propuesta: crear el repago solo
con la factura pagada por completo y señalar los parciales), artículo y cuentas nuevos vs. reutilizar
`PREST-PER` con vínculo al préstamo, y aprobación para implementar los pasos 1–3.

## 7. Procedimiento en producción

Desde el directorio `sites` del bench. Todo es simulación (se revierte) salvo `--commit`.

1. Respaldo de la base de datos.
2. Producto *Prestamo FAM Empleados*: `validate_normal_repayment = 0` (los colegiados pagan cuotas
   redondeadas; el script se detiene si está activo).
3. Clientes:
   `../env/bin/python -W ignore ../apps/colprocah_colegio/scripts/fix_fam_loan_customers.py --site <sitio> [--commit]`
4. Reporte, revisar `loans.csv`, `events.csv`, `unmatched.csv`:
   `../env/bin/python -W ignore ../apps/colprocah_colegio/scripts/fam_legacy_loans.py report --site <sitio> --cutover <AAAA-MM-DD> --since 2026-01-01 --out <dir>`
5. Crear préstamos (revisar `build.csv`):
   `... fam_legacy_loans.py build --site <sitio> --cutover <fecha> --since 2026-01-01 --out <dir> [--commit]`
6. Asiento de reclasificación en borrador (`--submit` para validarlo):
   `... fam_legacy_loans.py reclass --site <sitio> --cutover <fecha> --since 2026-01-01 --out <dir> [--commit] [--submit]`
7. Desde el corte, Lending registra los préstamos 2026: procesar intereses y cuotas desde la fecha de corte
   y registrar los pagos según la sección 6.

`build` omite préstamos ya existentes (por número de préstamo) y `reclass` se niega a crear un segundo
asiento. Los CSV contienen datos de colegiados: no se suben al repositorio.

## 8. Otros scripts

- `cancel_loan.py <loan> [<loan> ...] [--site <sitio>] [--commit]`: anula préstamos y todos sus documentos
  de Lending. Revierte los asientos de los préstamos que los tienen; a los reconstruidos sin asientos no
  intenta revertirles nada. Al anular, Lending encola reprocesos que fallan sin efecto
  ("No se puede vincular al documento anulado") y dejan registros en Error Log.
- `rebuild_048_25.py`: primera reconstrucción manual de 048/25; la reemplaza `fam_legacy_loans.py build`.

## 9. Pendientes

- Implementar el plan de la sección 6 (con las decisiones pendientes).
- Confirmar con FAM los casos 2026 dudosos (003/26, 018/26) y los préstamos de empleadas por planilla.
- Recargo por mora: Lending no cobró recargo en cuotas pagadas con atraso (producto: 24 %, 5 días de
  gracia); no se investigó.
- En producción el campo `naming_series` de Loan existe (personalizado); el script solo asigna la serie
  cuando falta el campo.
